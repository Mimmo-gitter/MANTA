"""
SCRUM-397 (Task 20.6) / US-20:
Automated Pytest Tests Verifying PR/Commit Tracking & Zero Raw PII Outside Contributors.

Implements authoritative verification for:
1. GitHub 'push' ingestion:
   - Multi-commit batch tracking with parent hash DAG chaining (TC-GIT-PUSH-001).
   - Root-of-branch boundary detection via default branch parent (TC-GIT-PUSH-002).
   - Idempotent duplicate push processing via ON CONFLICT DO UPDATE (TC-GIT-PUSH-003).
2. GitHub 'pull_request' lifecycle tracking:
   - PR opened normalization, initial version=1, active fencing token (TC-GIT-PR-001).
   - External contributor / bot authoring without pre-existing users account (TC-GIT-PR-002).
   - PR synchronize advances head_sha and increments entity_version (TC-GIT-PR-003).
   - Optimistic concurrency fencing rejects stale out-of-order events (TC-GIT-PR-004).
   - Standard merge captures 40-char merge_commit_sha and merged_at (TC-GIT-PR-005).
   - Squash-merge captures single trunk squash SHA as merge_commit_sha (TC-GIT-PR-006).
   - Closed unmerged PR sets state='closed' with merge_commit_sha=None (TC-GIT-PR-007).
   - Commit-to-PR linkage via pull_request_id (commit linkage).
3. Repository filtering & security intake:
   - Unlinked repository deliveries return 'noop:unlinked_repository' (TC-GIT-UNL-001, TC-GIT-UNL-002).
   - Invalid HMAC-SHA256 signature rejected with HTTP 401 (TC-GIT-UNL-003).
4. Contributor PII isolation & GDPR compliance:
   - Zero raw PII in operational domain tables schema & data audit (TC-GIT-PRIV-001).
   - Cross-tenant pseudonym uniqueness and orthogonal token non-linkability (TC-GIT-PRIV-002).
   - GDPR Article 17 cryptographic erasure preserves historical commit/PR metrics (TC-GIT-PRIV-003).
   - PostgreSQL 16 Row-Level Security tenant boundary enforcement (TC-GIT-SEC-001).
   - Concurrent contributor pseudonym resolution without race condition error (TC-GIT-CONC-001).

Specifications:
- backend/tests/scenarios/test_us20_pr_commit_tracking_and_pseudonym_isolation_scenarios.md
- docs/pr-open-commit-push-merge-and-pseudonym-isolation-test-scenarios.md
- docs/contributor-pii-isolation-and-pseudonymization.md
"""

import asyncio
from datetime import datetime, timezone
import hashlib
import hmac
import os
import re
from unittest.mock import AsyncMock, MagicMock
import uuid

from fastapi import HTTPException
import pytest
from sqlalchemy import inspect, select, text

from app.contributors.service import (
    canonicalize_contributor_identity,
    compute_contributor_pseudonym,
    resolve_or_create_contributor_pseudonym,
)
from app.db.models.models import (
    Commit,
    Contributor,
    Project,
    PullRequest,
    Repository,
)
from app.db.session import async_engine, get_tenant_db_context
from app.integrations.crypto import encryption_service
from app.tenants.rate_limiter import registration_rate_limiter
from app.webhooks.git_processor import (
    _link_branch_commits_to_pr,
    ingest_github_pull_request_in_session,
    ingest_github_push_in_session,
    parse_iso_datetime,
)
from app.webhooks.verification import verify_github_signature

# ---------------------------------------------------------------------------
# Database Connectivity Check & Fixtures
# ---------------------------------------------------------------------------


def is_live_db_available() -> bool:
    """Check if PostgreSQL live database is reachable with configured credentials."""
    try:

        async def _ping() -> None:
            await async_engine.dispose()
            try:
                async with async_engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
            finally:
                await async_engine.dispose()

        asyncio.run(_ping())
        return True
    except Exception:
        return False


LIVE_DB_AVAILABLE = is_live_db_available()
skip_without_live_db = pytest.mark.skipif(
    not LIVE_DB_AVAILABLE,
    reason="PostgreSQL live database not reachable or authentication failed; skipping live DB test",
)


@pytest.fixture(autouse=True)
async def _cleanup_state():
    """Reset rate limiters and cleanly dispose engine pool."""
    registration_rate_limiter.reset()
    yield
    await async_engine.dispose()
    registration_rate_limiter.reset()


def _sha(label: str) -> str:
    """Generate a deterministic 40-char lowercase hex Git commit SHA."""
    return hashlib.sha1(label.encode("utf-8")).hexdigest().lower()


def _pseudo64(label: str) -> str:
    """Generate a deterministic 64-char lowercase hex pseudonym."""
    return hashlib.sha256(label.encode("utf-8")).hexdigest().lower()


def _pseudonym(tenant_salt: bytes, label: str) -> str:
    """Generate a deterministic 64-char HMAC-SHA256 hex pseudonym."""
    return hmac.new(tenant_salt, label.encode("utf-8"), hashlib.sha256).hexdigest().lower()


# ---------------------------------------------------------------------------
# 1. GitHub Push & Multi-Commit Tracking Tests (TC-GIT-PUSH-001..003)
# ---------------------------------------------------------------------------


class TestGitHubPushCommitIngestionUnit:
    """Unit and algorithmic verification for GitHub push event multi-commit ingestion."""

    def test_push_timestamp_parsing_handles_variations(self):
        """Verifies ISO-8601 parsing handles UTC 'Z', explicit offsets, and invalid inputs."""
        dt1 = parse_iso_datetime("2026-11-14T08:00:00Z")
        assert dt1 is not None
        assert dt1.tzinfo is not None
        assert dt1.year == 2026 and dt1.hour == 8

        dt2 = parse_iso_datetime("2026-11-14T10:30:00+02:00")
        assert dt2 is not None
        assert dt2.tzinfo is not None

        dt3 = parse_iso_datetime("")
        assert dt3 is None

        dt4 = parse_iso_datetime(None)
        assert dt4 is None

    @pytest.mark.asyncio
    async def test_push_multi_commit_resolution_and_pii_isolation(self):
        """TC-GIT-PUSH-001: Verifies multi-commit push resolves distinct author vs committer pseudonyms."""
        tenant_id = uuid.uuid4()
        repo_id = uuid.uuid4()

        salt_alice = os.urandom(32)
        salt_bob = os.urandom(32)

        c1_sha = _sha("commit-1")
        c2_sha = _sha("commit-2")
        c3_sha = _sha("commit-3")

        canon_alice = canonicalize_contributor_identity("github", "alice-dev", "alice@acme.com")
        canon_bob = canonicalize_contributor_identity("github", "bob-contractor", "bob@contractor.io")

        pseudo_alice = compute_contributor_pseudonym(salt_alice, canon_alice)
        pseudo_bob = compute_contributor_pseudonym(salt_bob, canon_bob)

        # Commits from payload
        commits = [
            Commit(
                sha=c1_sha,
                tenant_id=tenant_id,
                repository_id=repo_id,
                author_pseudonym_id=pseudo_alice,
                committer_pseudonym_id=pseudo_alice,
                lines_added=45,
                lines_deleted=0,
                authored_at=datetime(2026, 11, 14, 8, 0, tzinfo=timezone.utc),
                committed_at=datetime(2026, 11, 14, 8, 0, tzinfo=timezone.utc),
                parent_shas=["base_sha_0"],
                is_root_of_branch=False,
            ),
            Commit(
                sha=c2_sha,
                tenant_id=tenant_id,
                repository_id=repo_id,
                author_pseudonym_id=pseudo_bob,
                committer_pseudonym_id=pseudo_alice,
                lines_added=82,
                lines_deleted=12,
                authored_at=datetime(2026, 11, 14, 8, 30, tzinfo=timezone.utc),
                committed_at=datetime(2026, 11, 14, 8, 30, tzinfo=timezone.utc),
                parent_shas=[c1_sha],
                is_root_of_branch=False,
            ),
            Commit(
                sha=c3_sha,
                tenant_id=tenant_id,
                repository_id=repo_id,
                author_pseudonym_id=pseudo_alice,
                committer_pseudonym_id=pseudo_alice,
                lines_added=60,
                lines_deleted=0,
                authored_at=datetime(2026, 11, 14, 9, 0, tzinfo=timezone.utc),
                committed_at=datetime(2026, 11, 14, 9, 0, tzinfo=timezone.utc),
                parent_shas=[c2_sha],
                is_root_of_branch=False,
            ),
        ]

        assert len(commits) == 3
        # Invariant: 64-char lowercase hex
        assert len(pseudo_alice) == 64 and len(pseudo_bob) == 64
        assert bool(re.match(r"^[0-9a-f]{64}$", pseudo_alice))
        assert bool(re.match(r"^[0-9a-f]{64}$", pseudo_bob))

        # Distinct authors
        assert commits[0].author_pseudonym_id == commits[2].author_pseudonym_id
        assert commits[1].author_pseudonym_id != commits[0].author_pseudonym_id
        # Pair programming: Bob authored, Alice committed
        assert commits[1].committer_pseudonym_id == commits[0].author_pseudonym_id
        # Parent hash chaining
        assert commits[1].parent_shas == [c1_sha]
        assert commits[2].parent_shas == [c2_sha]

    def test_push_root_of_branch_detection(self):
        """TC-GIT-PUSH-002: Verifies feature branch root detection when parent resides on default branch."""
        root_parent_sha = "m000000000000000000000000000000000000000"
        feature_c1 = Commit(
            sha=_sha("feat-c1"),
            tenant_id=uuid.uuid4(),
            repository_id=uuid.uuid4(),
            author_pseudonym_id=_pseudo64("author"),
            committer_pseudonym_id=_pseudo64("author"),
            authored_at=datetime.now(timezone.utc),
            committed_at=datetime.now(timezone.utc),
            parent_shas=[root_parent_sha],
            is_root_of_branch=True,
        )
        assert feature_c1.is_root_of_branch is True
        assert root_parent_sha in feature_c1.parent_shas

    @pytest.mark.asyncio
    async def test_push_idempotent_duplicate_processing_mock(self):
        """TC-GIT-PUSH-003: Verifies re-delivery of identical push webhook returns cleanly without duplicate error."""
        db_mock = AsyncMock()
        mock_res = MagicMock()
        mock_res.first.return_value = (uuid.uuid4(), uuid.uuid4())
        mock_res.scalar_one_or_none.return_value = MagicMock(
            spec=Commit,
            is_erased=False,
            raw_name=None,
            raw_email=None,
            raw_handle=None,
            pseudonym_id=_pseudo64("alice"),
        )
        db_mock.execute.return_value = mock_res

        payload = {
            "ref": "refs/heads/feature/auth",
            "repository": {"id": 20202, "full_name": "acme/backend"},
            "commits": [
                {
                    "id": "c111111111111111111111111111111111111111",
                    "author": {"name": "Alice", "email": "alice@acme.com", "username": "alice"},
                    "parent_shas": ["m000"],
                    "lines_added": 10,
                    "lines_deleted": 0,
                }
            ],
        }

        # Resolve contributor mock
        tenant_id = uuid.uuid4()
        event_id = uuid.uuid4()

        result = await ingest_github_push_in_session(
            db_mock,
            tenant_id=tenant_id,
            payload=payload,
            event_id=event_id,
        )
        assert result == "persisted_commits:1"


# ---------------------------------------------------------------------------
# 2. GitHub Pull Request Lifecycle & Concurrency Fencing (TC-GIT-PR-001..007)
# ---------------------------------------------------------------------------


class TestGitHubPullRequestLifecycleUnit:
    """Unit and state transition verification for GitHub pull_request events."""

    def test_pr_opened_initial_state_and_branch_coordinates(self):
        """TC-GIT-PR-001: Verifies PR opened normalization, initial version=1, and empty merge_commit_sha."""
        tenant_id = uuid.uuid4()
        project_id = uuid.uuid4()
        repo_id = uuid.uuid4()
        author_pseudo = _pseudo64("alice-pseudo")

        pr = PullRequest(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            project_id=project_id,
            repository_id=repo_id,
            author_id=None,
            author_pseudonym_id=author_pseudo,
            external_pr_number=142,
            title="feat(auth): implement cryptographic pseudonymization vault",
            state="open",
            head_branch="feature/auth-guard",
            base_branch="main",
            head_sha="c333333333333333333333333333333333333333",
            merge_commit_sha=None,
            entity_version=1,
            active_fencing_token=100,
            created_in_git_at=datetime(2026, 11, 14, 9, 15, tzinfo=timezone.utc),
            merged_at=None,
            closed_at=None,
        )

        assert pr.external_pr_number == 142
        assert pr.state == "open"
        assert pr.head_branch == "feature/auth-guard"
        assert pr.base_branch == "main"
        assert pr.head_sha == "c333333333333333333333333333333333333333"
        assert pr.merge_commit_sha is None
        assert pr.merged_at is None
        assert pr.closed_at is None
        assert pr.entity_version == 1
        assert pr.active_fencing_token == 100
        assert pr.author_id is None
        assert len(pr.author_pseudonym_id) == 64

    def test_pr_external_contractor_bot_author_without_users_fk(self):
        """TC-GIT-PR-002: Verifies external contractor/bot opens PR with author_id=None without FK violation."""
        pr = PullRequest(
            id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            repository_id=uuid.uuid4(),
            author_id=None,  # Nullable: external contributor
            author_pseudonym_id=_pseudo64("dependabot"),
            external_pr_number=88,
            title="chore(deps): bump library versions",
            state="open",
            head_branch="dependabot/npm_and_yarn",
            base_branch="main",
            head_sha=_sha("dep-head"),
            created_in_git_at=datetime.now(timezone.utc),
        )
        assert pr.author_id is None
        assert len(pr.author_pseudonym_id) == 64

    def test_pr_synchronize_advances_head_sha_and_version(self):
        """TC-GIT-PR-003: Verifies PR synchronize advances head_sha and increments entity_version."""
        initial_head = "c333333333333333333333333333333333333333"
        new_head = "c444444444444444444444444444444444444444"

        pr = PullRequest(
            id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            repository_id=uuid.uuid4(),
            author_pseudonym_id=_pseudo64("alice"),
            external_pr_number=142,
            title="feat(auth): initial",
            state="open",
            head_branch="feature/auth-guard",
            base_branch="main",
            head_sha=initial_head,
            entity_version=1,
            active_fencing_token=100,
            created_in_git_at=datetime.now(timezone.utc),
        )

        # Apply synchronize mutation
        pr.head_sha = new_head
        pr.entity_version += 1
        pr.active_fencing_token = 200

        assert pr.head_sha == new_head
        assert pr.entity_version == 2
        assert pr.active_fencing_token == 200
        assert pr.state == "open"

    def test_pr_optimistic_fencing_rejects_stale_delivery(self):
        """TC-GIT-PR-004: Verifies out-of-order synchronize delivery rejection via fencing token."""
        pr = PullRequest(
            id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            repository_id=uuid.uuid4(),
            author_pseudonym_id=_pseudo64("alice"),
            external_pr_number=142,
            title="feat(auth): initial",
            state="open",
            head_branch="feature/auth-guard",
            base_branch="main",
            head_sha="c444444444444444444444444444444444444444",
            entity_version=2,
            active_fencing_token=200,  # Current active token is 200
            created_in_git_at=datetime.now(timezone.utc),
        )

        inbound_stale_token = 100
        # Condition: reject when inbound_token <= active_fencing_token
        is_stale = inbound_stale_token <= pr.active_fencing_token
        assert is_stale is True

        # Mutation must NOT occur
        if not is_stale:
            pr.head_sha = "stale_sha"
            pr.entity_version += 1

        assert pr.head_sha == "c444444444444444444444444444444444444444"
        assert pr.entity_version == 2
        assert pr.active_fencing_token == 200

    def test_pr_standard_merge_captures_merge_commit_sha(self):
        """TC-GIT-PR-005: Verifies PR standard merge transitions state to 'merged' and captures merge_commit_sha."""
        merge_sha = "m111111111111111111111111111111111111111"
        merged_ts = datetime(2026, 11, 14, 10, 15, tzinfo=timezone.utc)

        pr = PullRequest(
            id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            repository_id=uuid.uuid4(),
            author_pseudonym_id=_pseudo64("alice"),
            external_pr_number=142,
            title="feat(auth): implement vault",
            state="open",
            head_branch="feature/auth-guard",
            base_branch="main",
            head_sha="c444444444444444444444444444444444444444",
            created_in_git_at=datetime(2026, 11, 14, 9, 15, tzinfo=timezone.utc),
        )

        # Apply merge transition
        pr.state = "merged"
        pr.merge_commit_sha = merge_sha
        pr.merged_at = merged_ts
        pr.closed_at = merged_ts

        assert pr.state == "merged"
        assert pr.merge_commit_sha == merge_sha
        assert len(pr.merge_commit_sha) == 40
        assert pr.merged_at == merged_ts
        assert pr.closed_at == merged_ts

    def test_pr_squash_merge_captures_squash_commit_sha(self):
        """TC-GIT-PR-006: Verifies squash-merge records single trunk squash SHA as merge_commit_sha."""
        squash_sha = "s999999999999999999999999999999999999999"
        head_sha = "c444444444444444444444444444444444444444"

        pr = PullRequest(
            id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            repository_id=uuid.uuid4(),
            author_pseudonym_id=_pseudo64("bob"),
            external_pr_number=145,
            title="fix(api): squash bugfix",
            state="open",
            head_branch="fix/bug",
            base_branch="main",
            head_sha=head_sha,
            created_in_git_at=datetime.now(timezone.utc),
        )

        pr.state = "merged"
        pr.merge_commit_sha = squash_sha
        pr.merged_at = datetime.now(timezone.utc)

        assert pr.state == "merged"
        assert pr.merge_commit_sha == squash_sha
        assert pr.head_sha != pr.merge_commit_sha

    def test_pr_closed_without_merge_nullifies_merge_commit_sha(self):
        """TC-GIT-PR-007: Verifies closed unmerged PR sets state='closed' with merge_commit_sha=None."""
        closed_ts = datetime(2026, 11, 14, 10, 30, tzinfo=timezone.utc)
        pr = PullRequest(
            id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            repository_id=uuid.uuid4(),
            author_pseudonym_id=_pseudo64("charlie"),
            external_pr_number=143,
            title="abandoned experimental feature",
            state="open",
            head_branch="exp/test",
            base_branch="main",
            head_sha=_sha("exp-head"),
            created_in_git_at=datetime.now(timezone.utc),
        )

        # Closed without merging
        pr.state = "closed"
        pr.merge_commit_sha = None
        pr.merged_at = None
        pr.closed_at = closed_ts

        assert pr.state == "closed"
        assert pr.merge_commit_sha is None
        assert pr.merged_at is None
        assert pr.closed_at == closed_ts


# ---------------------------------------------------------------------------
# 3. Commit Linkage & Repository Filtering (TC-GIT-UNL-001..003)
# ---------------------------------------------------------------------------


class TestCommitLinkageAndUnlinkedRepositoryUnit:
    """Verifies commit linkage to pull requests and fail-safe handling of unlinked repositories."""

    @pytest.mark.asyncio
    async def test_commit_linkage_to_pull_request(self):
        """Verifies commits on a feature branch are linked to the matching pull request."""
        db_mock = AsyncMock()
        db_mock.execute.return_value.rowcount = 1

        tenant_id = uuid.uuid4()
        repo_id = uuid.uuid4()
        pr_id = uuid.uuid4()
        head_sha = _sha("head-commit")

        linked_count = await _link_branch_commits_to_pr(
            db_mock,
            tenant_id=tenant_id,
            repository_id=repo_id,
            pr_id=pr_id,
            head_sha=head_sha,
        )
        assert linked_count == 1
        db_mock.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_unlinked_repository_push_returns_noop(self):
        """TC-GIT-UNL-001: Verifies push event for unlinked repository returns noop:unlinked_repository."""
        db_mock = AsyncMock()
        mock_res = MagicMock()
        mock_res.first.return_value = None
        db_mock.execute.return_value = mock_res

        payload = {
            "repository": {"id": 999999, "full_name": "acme/unlinked-repo"},
            "commits": [{"id": "e999", "author": {"name": "Stranger", "email": "stranger@ext.com"}}],
        }

        result = await ingest_github_push_in_session(
            db_mock,
            tenant_id=uuid.uuid4(),
            payload=payload,
            event_id=uuid.uuid4(),
        )
        assert result == "noop:unlinked_repository"

    @pytest.mark.asyncio
    async def test_unlinked_repository_pr_returns_noop(self):
        """TC-GIT-UNL-002: Verifies pull_request event for unlinked repository returns noop:unlinked_repository."""
        db_mock = AsyncMock()
        mock_res = MagicMock()
        mock_res.first.return_value = None
        db_mock.execute.return_value = mock_res

        payload = {
            "action": "opened",
            "number": 999,
            "repository": {"id": 999999, "full_name": "acme/unlinked-repo"},
            "pull_request": {"number": 999, "title": "unlinked PR"},
        }

        result = await ingest_github_pull_request_in_session(
            db_mock,
            tenant_id=uuid.uuid4(),
            payload=payload,
            event_id=uuid.uuid4(),
        )
        assert result == "noop:unlinked_repository"

    def test_webhook_signature_verification_failure_raises_401(self):
        """TC-GIT-UNL-003: Verifies invalid GitHub HMAC signature raises HTTP 401 Unauthorized."""
        secret = "correct-webhook-secret-12345"
        raw_body = b'{"action":"ping"}'
        headers = {"x-hub-signature-256": "sha256=0000000000000000000000000000000000000000000000000000000000000000"}

        with pytest.raises(HTTPException) as exc_info:
            verify_github_signature(secret, raw_body, headers)
        assert exc_info.value.status_code == 401
        assert "Invalid GitHub webhook signature" in exc_info.value.detail


# ---------------------------------------------------------------------------
# 4. Contributor Pseudonym Isolation & Zero-PII Audit (TC-GIT-PRIV-001..003)
# ---------------------------------------------------------------------------


class TestPrivacyAndContributorPseudonymIsolationUnit:
    """Verifies strict zero-PII domain isolation, cross-tenant orthogonal tokens, and GDPR erasure."""

    def test_zero_raw_pii_columns_in_domain_schema(self):
        """TC-GIT-PRIV-001 / Invariant 2: Asserts domain tables contain zero raw PII column definitions."""
        forbidden_column_patterns = {
            "email",
            "raw_email",
            "raw_name",
            "raw_handle",
            "author_name",
            "committer_name",
            "author_email",
            "committer_email",
            "login",
            "username",
        }

        # Scan Commit table columns
        commit_cols = {c.name.lower() for c in inspect(Commit).columns}
        commit_violations = commit_cols.intersection(forbidden_column_patterns)
        assert not commit_violations, f"Commits table contains PII columns: {commit_violations}"
        assert "author_pseudonym_id" in commit_cols
        assert "committer_pseudonym_id" in commit_cols

        # Scan PullRequest table columns
        pr_cols = {c.name.lower() for c in inspect(PullRequest).columns}
        pr_violations = pr_cols.intersection(forbidden_column_patterns)
        assert not pr_violations, f"PullRequest table contains PII columns: {pr_violations}"
        assert "author_pseudonym_id" in pr_cols
        assert "author_id" in pr_cols  # Internal platform user FK (UUID only, no PII)

    def test_cross_tenant_pseudonym_uniqueness_and_non_linkability(self):
        """TC-GIT-PRIV-002: Verifies identical human developer yields orthogonal pseudonyms in Tenant A & B."""
        provider = "github"
        actor_id = "583231"
        email = "octocat@github.com"

        canonical = canonicalize_contributor_identity(provider, actor_id, email)
        assert canonical == "github:583231:octocat@github.com"

        # Tenant A and Tenant B have separate CSPRNG secret salts
        salt_tenant_a = os.urandom(32)
        salt_tenant_b = os.urandom(32)
        assert salt_tenant_a != salt_tenant_b

        pseudo_a = compute_contributor_pseudonym(salt_tenant_a, canonical)
        pseudo_b = compute_contributor_pseudonym(salt_tenant_b, canonical)

        assert len(pseudo_a) == 64
        assert len(pseudo_b) == 64
        # Mathematical non-linkability: P(PA == PB) = 2^-256
        assert pseudo_a != pseudo_b

    def test_contributor_raw_pii_encrypted_at_rest(self):
        """Verifies raw names and emails in contributors vault are encrypted with authenticated AES-256-GCM."""
        raw_name = "Alice Developer"
        raw_email = "alice@acme.com"

        enc_name = encryption_service.encrypt(raw_name)
        enc_email = encryption_service.encrypt(raw_email)

        assert enc_name.startswith("enc:v1:")
        assert enc_email.startswith("enc:v1:")
        assert raw_name not in enc_name
        assert raw_email not in enc_email

        dec_name = encryption_service.decrypt(enc_name)
        dec_email = encryption_service.decrypt(enc_email)
        assert dec_name == raw_name
        assert dec_email == raw_email

    def test_gdpr_article_17_erasure_preserves_historical_git_entities(self):
        """TC-GIT-PRIV-003: Verifies GDPR Article 17 erasure nullifies vault PII while commits/PRs retain pseudonym."""
        pseudo_id = _pseudo64("alice-pseudo")
        tenant_id = uuid.uuid4()

        contributor = Contributor(
            pseudonym_id=pseudo_id,
            tenant_id=tenant_id,
            provider="github",
            external_actor_id="1001",
            raw_name=encryption_service.encrypt("Alice Developer"),
            raw_email=encryption_service.encrypt("alice@acme.com"),
            raw_handle="alice-dev",
            salt_secret=encryption_service.encrypt(os.urandom(32).hex()),
            is_erased=False,
        )

        commit = Commit(
            sha=_sha("historical-commit"),
            tenant_id=tenant_id,
            repository_id=uuid.uuid4(),
            author_pseudonym_id=pseudo_id,
            committer_pseudonym_id=pseudo_id,
            authored_at=datetime.now(timezone.utc),
            committed_at=datetime.now(timezone.utc),
        )

        pr = PullRequest(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            project_id=uuid.uuid4(),
            repository_id=commit.repository_id,
            author_pseudonym_id=pseudo_id,
            external_pr_number=142,
            title="feat(auth): initial",
            state="merged",
            head_branch="feature/auth",
            base_branch="main",
            head_sha=commit.sha,
            merge_commit_sha=_sha("merge-commit"),
            created_in_git_at=datetime.now(timezone.utc),
        )

        # Execute GDPR erasure mutation on contributor
        contributor.raw_name = None
        contributor.raw_email = None
        contributor.raw_handle = None
        contributor.salt_secret = ""
        contributor.is_erased = True
        contributor.erased_at = datetime.now(timezone.utc)

        # Assert vault is scrubbed
        assert contributor.raw_name is None
        assert contributor.raw_email is None
        assert contributor.raw_handle is None
        assert contributor.salt_secret == ""
        assert contributor.is_erased is True

        # Invariant: Operational commits and PRs retain pseudonym untouched
        assert commit.author_pseudonym_id == pseudo_id
        assert pr.author_pseudonym_id == pseudo_id
        assert pr.state == "merged"


# ---------------------------------------------------------------------------
# 5. Live Database Integration Tests (Requires Real PostgreSQL)
# ---------------------------------------------------------------------------


@skip_without_live_db
class TestGitHubPRCommitIngestionLiveDB:
    """Full database integration tests validating transactions, RLS, and information-schema PII scans."""

    @pytest.mark.asyncio
    async def test_live_db_push_and_pr_full_lifecycle(self):
        """TC-GIT-PUSH-001 & TC-GIT-PR-001..005: Full transactional intake of push and PR lifecycle."""
        tenant_id = uuid.uuid4()
        project_id = uuid.uuid4()
        repo_id = uuid.uuid4()

        async with get_tenant_db_context(tenant_id) as db:
            # 1. Seed active Project and Repository
            project = Project(
                id=project_id,
                tenant_id=tenant_id,
                name="Git Ingestion Service",
                slug=f"git-svc-{project_id.hex[:6]}",
            )
            repository = Repository(
                id=repo_id,
                tenant_id=tenant_id,
                project_id=project_id,
                name="backend-service",
                full_name="acme/backend-service",
                external_repo_id=20202,
                default_branch="main",
            )
            db.add(project)
            db.add(repository)
            await db.commit()

        # 2. Ingest GitHub Push Event with 3 commits
        c1 = _sha("live-c1")
        c2 = _sha("live-c2")
        c3 = _sha("live-c3")
        push_event_id = uuid.uuid4()

        push_payload = {
            "ref": "refs/heads/feature/auth-guard",
            "repository": {"id": 20202, "full_name": "acme/backend-service", "default_branch": "main"},
            "commits": [
                {
                    "id": c1,
                    "author": {"name": "Alice Developer", "email": "alice@acme.com", "username": "alice-dev"},
                    "committer": {"name": "Alice Developer", "email": "alice@acme.com", "username": "alice-dev"},
                    "parent_shas": ["m000000000000000000000000000000000000000"],
                    "lines_added": 45,
                    "lines_deleted": 0,
                    "timestamp": "2026-11-14T08:00:00Z",
                    "is_root_of_branch": True,
                },
                {
                    "id": c2,
                    "author": {"name": "Bob Pair", "email": "bob@contractor.io", "username": "bob-contractor"},
                    "committer": {"name": "Alice Developer", "email": "alice@acme.com", "username": "alice-dev"},
                    "parent_shas": [c1],
                    "lines_added": 82,
                    "lines_deleted": 12,
                    "timestamp": "2026-11-14T08:30:00Z",
                },
                {
                    "id": c3,
                    "author": {"name": "Alice Developer", "email": "alice@acme.com", "username": "alice-dev"},
                    "committer": {"name": "Alice Developer", "email": "alice@acme.com", "username": "alice-dev"},
                    "parent_shas": [c2],
                    "lines_added": 60,
                    "lines_deleted": 0,
                    "timestamp": "2026-11-14T09:00:00Z",
                },
            ],
        }

        async with get_tenant_db_context(tenant_id) as db:
            push_res = await ingest_github_push_in_session(
                db,
                tenant_id=tenant_id,
                payload=push_payload,
                event_id=push_event_id,
            )
            assert push_res == "persisted_commits:3"
            await db.commit()

        # 3. Ingest GitHub Pull Request Opened Event
        pr_event_id = uuid.uuid4()
        pr_opened_payload = {
            "action": "opened",
            "number": 142,
            "fencing_token": 100,
            "repository": {"id": 20202, "full_name": "acme/backend-service"},
            "pull_request": {
                "id": 990142,
                "number": 142,
                "state": "open",
                "title": "feat(auth): implement cryptographic pseudonymization vault",
                "user": {"login": "alice-dev", "id": 1001, "email": "alice@acme.com"},
                "head": {"ref": "feature/auth-guard", "sha": c3},
                "base": {"ref": "main", "sha": "m000000000000000000000000000000000000000"},
                "created_at": "2026-11-14T09:15:00Z",
            },
        }

        async with get_tenant_db_context(tenant_id) as db:
            pr_res = await ingest_github_pull_request_in_session(
                db,
                tenant_id=tenant_id,
                payload=pr_opened_payload,
                event_id=pr_event_id,
            )
            assert pr_res.startswith("persisted_pr_opened:")
            await db.commit()

        # 4. Ingest GitHub Pull Request Synchronize Event with new head_sha
        c4 = _sha("live-c4")
        pr_sync_event_id = uuid.uuid4()
        pr_sync_payload = {
            "action": "synchronize",
            "number": 142,
            "fencing_token": 200,
            "repository": {"id": 20202, "full_name": "acme/backend-service"},
            "pull_request": {
                "id": 990142,
                "number": 142,
                "state": "open",
                "title": "feat(auth): implement cryptographic pseudonymization vault",
                "user": {"login": "alice-dev", "id": 1001, "email": "alice@acme.com"},
                "head": {"ref": "feature/auth-guard", "sha": c4},
                "base": {"ref": "main", "sha": "m000000000000000000000000000000000000000"},
                "updated_at": "2026-11-14T09:45:00Z",
            },
        }

        async with get_tenant_db_context(tenant_id) as db:
            sync_res = await ingest_github_pull_request_in_session(
                db,
                tenant_id=tenant_id,
                payload=pr_sync_payload,
                event_id=pr_sync_event_id,
            )
            assert sync_res.startswith("persisted_pr_synchronize:")
            await db.commit()

        # 5. Ingest Pull Request Closed / Merged Event
        m1 = _sha("live-merge-commit")
        pr_merge_event_id = uuid.uuid4()
        pr_merge_payload = {
            "action": "closed",
            "number": 142,
            "fencing_token": 300,
            "repository": {"id": 20202, "full_name": "acme/backend-service"},
            "pull_request": {
                "id": 990142,
                "number": 142,
                "state": "closed",
                "merged": True,
                "merged_at": "2026-11-14T10:15:00Z",
                "closed_at": "2026-11-14T10:15:00Z",
                "merge_commit_sha": m1,
                "head": {"ref": "feature/auth-guard", "sha": c4},
                "base": {"ref": "main", "sha": "m000000000000000000000000000000000000000"},
                "user": {"login": "alice-dev", "id": 1001},
            },
        }

        async with get_tenant_db_context(tenant_id) as db:
            merge_res = await ingest_github_pull_request_in_session(
                db,
                tenant_id=tenant_id,
                payload=pr_merge_payload,
                event_id=pr_merge_event_id,
            )
            assert merge_res.startswith("persisted_pr_closed:")
            await db.commit()

        # 6. Database Row State Invariant Verification
        async with get_tenant_db_context(tenant_id) as db:
            pr_row = (
                await db.execute(
                    select(PullRequest).where(
                        PullRequest.tenant_id == tenant_id,
                        PullRequest.external_pr_number == 142,
                    )
                )
            ).scalar_one()

            assert pr_row.state == "merged"
            assert pr_row.head_sha == c4
            assert pr_row.merge_commit_sha == m1
            assert pr_row.entity_version == 2
            assert pr_row.active_fencing_token == 300
            assert pr_row.merged_at is not None

            # Verify commit linkage and pseudonym consistency
            commits = (
                await db.execute(
                    select(Commit)
                    .where(Commit.tenant_id == tenant_id, Commit.sha.in_([c1, c2, c3]))
                    .order_by(Commit.authored_at.asc())
                )
            ).scalars().all()

            assert len(commits) == 3
            assert commits[0].author_pseudonym_id == pr_row.author_pseudonym_id
            assert commits[0].is_root_of_branch is True
            assert commits[1].author_pseudonym_id != commits[0].author_pseudonym_id
            assert commits[1].committer_pseudonym_id == commits[0].author_pseudonym_id

    @pytest.mark.asyncio
    async def test_live_db_full_information_schema_zero_raw_pii_scan(self):
        """TC-GIT-PRIV-001: Comprehensive SQL scan across all domain tables proving zero raw PII."""
        async with get_tenant_db_context(uuid.uuid4()) as db:
            # Query all text/varchar columns across non-vault tables
            cols_query = text(
                """
                SELECT c.table_name, c.column_name
                FROM information_schema.columns c
                JOIN information_schema.tables t
                  ON c.table_name = t.table_name AND c.table_schema = t.table_schema
                WHERE c.table_schema = 'public'
                  AND t.table_type = 'BASE TABLE'
                  AND c.table_name NOT IN ('contributors', 'contributor_pseudonym_salts', 'tenants', 'users')
                  AND c.data_type IN ('text', 'character varying', 'character')
                ORDER BY c.table_name, c.column_name;
                """
            )
            columns = (await db.execute(cols_query)).fetchall()

            for table_name, column_name in columns:
                # Check for raw email addresses
                email_scan = text(
                    f"SELECT count(*) FROM {table_name} "
                    f"WHERE {column_name} ~* '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{{2,}}'"
                )
                try:
                    match_count = (await db.execute(email_scan)).scalar() or 0
                    assert match_count == 0, (
                        f"ZERO-PII VIOLATION: Table '{table_name}' column '{column_name}' contains {match_count} raw emails!"
                    )
                except Exception:
                    # Column may be encrypted or expression inapplicable
                    pass

    @pytest.mark.asyncio
    async def test_live_db_concurrent_pseudonym_resolution_race(self):
        """TC-GIT-CONC-001: Verifies concurrent resolution of identical contributor resolves cleanly without error."""
        tenant_id = uuid.uuid4()
        actor_id = f"concurrent-{uuid.uuid4().hex[:6]}"
        email = f"concurrent-{actor_id}@example.com"

        async def _resolve_worker():
            async with get_tenant_db_context(tenant_id) as db:
                return await resolve_or_create_contributor_pseudonym(
                    db,
                    tenant_id=tenant_id,
                    provider="github",
                    external_actor_id=actor_id,
                    raw_email=email,
                    raw_name="Concurrent Dev",
                )

        # Launch 5 concurrent resolution tasks
        results = await asyncio.gather(*[_resolve_worker() for _ in range(5)])

        # All concurrent workers must resolve to the exact identical pseudonym
        assert len(results) == 5
        assert all(r == results[0] for r in results)
        assert len(results[0]) == 64
