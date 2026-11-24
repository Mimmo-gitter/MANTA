# Інструкція для розробника / DevOps: Продакшн-налаштування Keycloak, Backend, CORS та секретів (`TASK-301-5`)

> **Контекст:** `TASK-301-5` (`SCRUM-500` / `STORY-301` — `SRS-CD-001`)  
> **Призначення:** покроковий runbook для завершення продакшн-налаштування MANTA за реверс-проксі Caddy (HTTPS), налаштування GitHub Secrets, ротації секретів, запуску чистих міграцій БД (`SEED_DEV_DATA=false`) та перевірки готовності сервісів.

---

## 1. Що вже зроблено в коді репозиторію

Уся кодова та конфігураційна база вже готова в репозиторії (при цьому локальний dev і CI-пайплайн залишилися на 100% сумісними):

| Компонент | Файл у репозиторії | Що реалізовано |
| :--- | :--- | :--- |
| **Keycloak Production Realm Overlay** | `infra/keycloak/realm-prod-overlay.json`<br>`infra/keycloak/render_prod_realm.py` | Продакшн-оверлей поверх `realm-export.json`: `sslRequired: "external"`, `registrationAllowed: false`, продакшн-таймаути токенів/сесій (`900s` / `1800s` / `36000s`), `https://manta.<domain>/*` для redirect і post-logout URIs, PKCE `S256`, `users: []` (0 dev-користувачів). |
| **Keycloak & Stack Production Compose** | `backend/docker-compose.prod.yml` | Переводить Keycloak із `start-dev` у `start --import-realm` (`KC_HOSTNAME`, `KC_PROXY_HEADERS=xforwarded`, `KC_HTTP_ENABLED=true`, `KC_HOSTNAME_STRICT_HTTPS=true`), рендерить продакшн-реалм через `keycloak-realm-init`, вимикає `mailpit`, прибирає прямі публічні порти, вимагає всі секрети у fail-fast режимі `${VAR:?...}`. |
| **Backend Proxy Headers** | `backend/Dockerfile` | Додано `--proxy-headers --forwarded-allow-ips=*` у `uvicorn` для коректної обробки `X-Forwarded-Proto`, `X-Forwarded-For`, `X-Forwarded-Host` від Caddy. |
| **CORS & Fail-Fast Secrets Guard** | `backend/app/config.py`<br>`backend/app/main.py` | При `APP_ENV=production` автоматично вимикає localhost CORS regex (дозволені лише явні `https://` із `CORS_ORIGINS`) та блокує старт бекенду, якщо лишилися dev-секрети, `localhost` URL або `SEED_DEV_DATA=true`. |
| **Продакшн-міграції без dev-сіду** | `backend/scripts/run_prod_migrations.py` | Окремий production-раннер: падає при `SEED_DEV_DATA=true`, запускає виключно `alembic upgrade head` (без `seed_dev_db`) та синхронізує ротований SCRAM-SHA-256 пароль ролі `eng_app_user` у PostgreSQL. |
| **Шаблон продакшн `.env`** | `backend/.env.prod.example` | Повний перелік змінних середовища без реальних секретів (`.env.prod` ігнорується Git-ом). |
| **Автоматизовані тести** | `backend/tests/test_prod_hardening.py` | 29 тестів, які валідують `Dockerfile`, рендер реалму, `docker-compose.prod.yml`, CORS/секрет-валідатор, `run_prod_migrations.py` і `.env.prod.example`. |

---

## 2. Передумови перед деплоєм

Перед початком переконайтеся, що на цільовому сервері (VPS) підготовлено:

1. **Docker Engine** та **Docker Compose v2.24+** (обов'язково для підтримки `!reset` у `backend/docker-compose.prod.yml`):
   ```bash
   docker compose version
   ```
2. **Публічний домен** (`<domain>`, наприклад `example.com`) із налаштованими DNS A/AAAA-записами на IP сервера:
   - `manta.<domain>` — Web Portal (Next.js)
   - `api.manta.<domain>` — FastAPI Backend
   - `auth.manta.<domain>` — Keycloak Identity Provider
3. **Caddy** (TLS reverse proxy), підключений до спільної Docker-мережі `manta_net` (відкриті назовні лише порти `80/tcp` і `443/tcp`).
4. **SMTP-провайдер** (SES, Postmark, Mailgun, SendGrid тощо) для листів-запрошень (`mailpit` у проді вимкнено).

---

## 3. Крок 1 — Генерація ротованих продакшн-секретів

Виконайте команди нижче на захищеній робочій станції (або на сервері) та збережіть значення в менеджері секретів. **Ніколи не комітьте згенеровані значення у Git.**

```bash
# 1. ENCRYPTION_MASTER_KEY — рівно 64 hex-символи (AES-256-GCM)
echo "ENCRYPTION_MASTER_KEY=$(openssl rand -hex 32)"

# 2. POSTGRES_PASSWORD — адмін-пароль PostgreSQL, PgBouncer (manta_user) та БД Keycloak
echo "POSTGRES_PASSWORD=$(openssl rand -base64 32 | tr -d '/+=' | head -c 32)"

# 3. ENG_APP_DB_PASSWORD — окремий пароль для least-privilege ролі eng_app_user (NOBYPASSRLS)
echo "ENG_APP_DB_PASSWORD=$(openssl rand -base64 32 | tr -d '/+=' | head -c 32)"

# 4. KEYCLOAK_ADMIN_PASSWORD — пароль адміністратора Keycloak (realm master, >= 12 символів)
echo "KEYCLOAK_ADMIN_PASSWORD=$(openssl rand -base64 32 | tr -d '/+=' | head -c 32)"
```

> **Чому `tr -d '/+='`:** видалення спецсимволів URL дозволяє безпечно вставляти паролі в `DATABASE_URL` і `MIGRATION_DATABASE_URL` без ризику помилок URL-екранування.

---

## 4. Крок 2 — Налаштування GitHub Secrets

Відкрийте у GitHub: **Repository $\rightarrow$ Settings $\rightarrow$ Secrets and variables $\rightarrow$ Actions** (рекомендовано додати у GitHub Environment `production`) і створіть усі секрети з таблиці нижче:

| Назва секрету в GitHub | Приклад значення | Призначення |
| :--- | :--- | :--- |
| `MANTA_DOMAIN` | `example.com` | Базовий домен; рендерить `https://manta.<domain>/*` у Keycloak |
| `KC_HOSTNAME` | `auth.manta.example.com` | Публічний hostname Keycloak за Caddy |
| `CORS_ORIGINS` | `https://manta.example.com` | Явний список HTTPS origin-ів порталу (через кому, без `*` і `localhost`) |
| `OIDC_ISSUER` | `https://auth.manta.example.com/realms/manta` | Публічний JWT `iss`, який перевіряє FastAPI |
| `OIDC_JWKS_URL` | `http://keycloak:8080/realms/manta/protocol/openid-connect/certs` | Внутрішній URL у Docker-мережі для швидкого фетчу JWKS (або публічний `https://...`) |
| `WEB_BASE_URL` | `https://manta.example.com` | Базовий URL фронтенду для посилань у листах-запрошеннях |
| `WEBHOOK_BASE_URL` | `https://api.manta.example.com` | Базовий URL API для генерації webhook callback URLs |
| `ENCRYPTION_MASTER_KEY` | `<64 hex з Кроку 1>` | Єдиний ключ AES-256-GCM для `app` та `worker` |
| `POSTGRES_PASSWORD` | `<пароль з Кроку 1>` | Пароль користувача `manta_user` у Postgres, PgBouncer та Keycloak DB |
| `ENG_APP_DB_PASSWORD` | `<пароль з Кроку 1>` | Пароль для ролі `eng_app_user` (автоматично синхронізується скриптом міграцій) |
| `DATABASE_URL` | `postgresql+asyncpg://eng_app_user:<ENG_APP_DB_PASSWORD>@pgbouncer:5432/manta_db` | Робоче підключення `app`/`worker`/`sweeper`/`outbox-relay` через PgBouncer |
| `MIGRATION_DATABASE_URL` | `postgresql+asyncpg://manta_user:<POSTGRES_PASSWORD>@postgres:5432/manta_db` | Пряме адмін-підключення для `alembic upgrade head` |
| `KEYCLOAK_ADMIN_PASSWORD` | `<пароль з Кроку 1>` | Пароль адміна Keycloak |
| `SMTP_HOST` | `smtp.provider.com` | Хост продакшн SMTP-сервера |
| `SMTP_PORT` | `587` | Порт SMTP (`587` для STARTTLS) |
| `SMTP_USER` | `...` | Логін SMTP |
| `SMTP_PASSWORD` | `...` | Пароль / API-ключ SMTP |
| `SMTP_FROM` | `noreply@manta.example.com` | Адреса відправника |

> **Важливо щодо PgBouncer:** у файлі `infra/pgbouncer/pgbouncer.ini` зафіксовано `auth_user = manta_user`. Тому в проді **залишайте `POSTGRES_USER=manta_user`** із новим сильним `POSTGRES_PASSWORD`, а для `eng_app_user` використовуйте окремий `ENG_APP_DB_PASSWORD`.

---

## 5. Крок 3 — Налаштування Caddy (Reverse Proxy)

Caddy автоматично виставляє `X-Forwarded-For`, `X-Forwarded-Proto` та `X-Forwarded-Host` при використанні `reverse_proxy`.

### Варіант A — Окремі піддомени (рекомендовано, дефолт у `.env.prod.example`)

```caddyfile
manta.{$MANTA_DOMAIN} {
    reverse_proxy web:3000
}

api.manta.{$MANTA_DOMAIN} {
    reverse_proxy app:8000
}

auth.manta.{$MANTA_DOMAIN} {
    reverse_proxy keycloak:8080
}
```

Змінні в `.env.prod`:
```env
MANTA_DOMAIN=example.com
KC_HOSTNAME=auth.manta.example.com
KC_HTTP_RELATIVE_PATH=/
OIDC_ISSUER=https://auth.manta.example.com/realms/manta
OIDC_JWKS_URL=http://keycloak:8080/realms/manta/protocol/openid-connect/certs
```

### Варіант B — Єдиний домен із subpath `/auth` для Keycloak (альтернатива)

Якщо Keycloak працює під `https://manta.<domain>/auth`:
```caddyfile
manta.{$MANTA_DOMAIN} {
    handle /auth* {
        reverse_proxy keycloak:8080
    }
    handle /api/* {
        reverse_proxy app:8000
    }
    handle {
        reverse_proxy web:3000
    }
}
```

Тоді в `.env.prod` задайте:
```env
KC_HOSTNAME=manta.example.com
KC_HTTP_RELATIVE_PATH=/auth
OIDC_ISSUER=https://manta.example.com/auth/realms/manta
OIDC_JWKS_URL=http://keycloak:8080/auth/realms/manta/protocol/openid-connect/certs
WEBHOOK_BASE_URL=https://manta.example.com
```

---

## 6. Крок 4 — Створення `backend/.env.prod` на сервері

Скопіюйте шаблон і заповніть реальними значеннями (або згенеруйте цей файл у CD-воркфлоу з GitHub Secrets):

```bash
cp backend/.env.prod.example backend/.env.prod
chmod 600 backend/.env.prod
```

Обов'язково перевірте перед запуском, що в `backend/.env.prod`:
- `APP_ENV=production`
- `SEED_DEV_DATA=false`
- Не залишилося жодного `<REPLACE_...>` плейсхолдера
- Немає `localhost`, `127.0.0.1`, `admin123`, `manta_password`, `eng_app_pass_change_in_prod` або `0123456789abcdef...`

---

## 7. Крок 5 — Валідація та запуск продакшн-стеку

```bash
# 1. Fail-fast перевірка, що всі обов'язкові секрети задані й Compose коректно зливається
docker compose --env-file backend/.env.prod \
  -f backend/docker-compose.yml \
  -f backend/docker-compose.prod.yml config > /dev/null

# 2. Збірка та запуск у продакшн-режимі
docker compose --env-file backend/.env.prod \
  -f backend/docker-compose.yml \
  -f backend/docker-compose.prod.yml up -d --build
```

### Порядок автоматичної ініціалізації при старті

1. **`postgres`** та **`valkey`** піднімаються і проходять healthcheck (без відкритих портів на хост).
2. **`keycloak-realm-init`** (одноразовий контейнер) запускає `infra/keycloak/render_prod_realm.py` і створює `/rendered/realm-export.json` у спільному volume `keycloak_rendered_realm`.
3. **`db-init`** (одноразовий контейнер) запускає `python -m scripts.run_prod_migrations`:
   - перевіряє, що `SEED_DEV_DATA=false`;
   - виконує **лише** `alembic upgrade head`;
   - оновлює SCRAM-SHA-256 пароль ролі `eng_app_user` у PostgreSQL відповідно до `ENG_APP_DB_PASSWORD`.
4. **`keycloak`** стартує в режимі `start --import-realm` із `KC_PROXY_HEADERS=xforwarded` і `KC_HTTP_ENABLED=true`, імпортуючи виключно відрендерений продакшн-реалм.
5. **`pgbouncer`** стартує з ротованим `POSTGRES_PASSWORD`.
6. **`app`**, **`worker`**, **`sweeper`**, **`outbox-relay`** стартують після завершення `db-init` і готовності `pgbouncer`/`valkey`/`keycloak`. На старті `app` виконує `settings.assert_production_security()` — якщо якийсь секрет слабкий або не заданий, контейнер одразу впаде з чітким повідомленням у логах.

---

## 8. Крок 6 — Чекліст перевірки Acceptance Criteria на живому сервері

Виконайте всі пункти нижче після деплою:

### 8.1. Keycloak у продакшн-режимі `start` за Caddy
```bash
# Перевірка логів Keycloak (має бути production mode, без згадок start-dev)
docker compose --env-file backend/.env.prod \
  -f backend/docker-compose.yml -f backend/docker-compose.prod.yml logs keycloak | head -n 40

# Перевірка OIDC Discovery — усі URL повинні бути HTTPS з вашим доменом
curl -sf "https://${KC_HOSTNAME}/realms/manta/.well-known/openid-configuration" | \
  jq '{issuer, authorization_endpoint, token_endpoint, end_session_endpoint, jwks_uri}'
```
Очікується:
- `issuer` == `https://auth.manta.<domain>/realms/manta` (або `/auth/realms/manta` для Варіанту B).
- У Keycloak Admin Console (`https://<KC_HOSTNAME>/admin/`) для реалму `manta`:
  - **Realm settings $\rightarrow$ Login**: `Require SSL = External requests` (`external`), `User registration = Off`.
  - **Realm settings $\rightarrow$ Tokens / Sessions**: Access Token Lifespan = `15 Minutes` (`900s`), SSO Session Idle = `30 Minutes` (`1800s`), SSO Session Max = `10 Hours` (`36000s`).
  - **Clients $\rightarrow$ `manta-portal`**: Valid redirect URIs = `https://manta.<domain>/*`, Valid post logout redirect URIs = `https://manta.<domain>/*`, Web origins = `https://manta.<domain>` (жодного `localhost` чи `*`).
  - **Users**: порожньо (`0` dev-користувачів).

### 8.2. Backend proxy headers і строгий CORS
```bash
# 1. Healthcheck через публічний HTTPS (Caddy -> app)
curl -sfI "https://api.manta.${MANTA_DOMAIN}/health"

# 2. Дозволений CORS preflight для продакшн-порталу (очікується access-control-allow-origin: https://manta.<domain>)
curl -si -X OPTIONS "https://api.manta.${MANTA_DOMAIN}/api/v1/auth/me" \
  -H "Origin: https://manta.${MANTA_DOMAIN}" \
  -H "Access-Control-Request-Method: GET" \
  -H "Access-Control-Request-Headers: Authorization" | grep -i "access-control"

# 3. Заборонений CORS preflight з localhost (НЕ повинно бути access-control-allow-origin)
curl -si -X OPTIONS "https://api.manta.${MANTA_DOMAIN}/api/v1/auth/me" \
  -H "Origin: http://localhost:3000" \
  -H "Access-Control-Request-Method: GET" | grep -i "access-control" || echo "OK: localhost blocked"
```

### 8.3. Чисті продакшн-міграції БД (`SEED_DEV_DATA=false`)
```bash
# Перевірити логи db-init (має бути тільки alembic upgrade head + sync eng_app_user, без seed_dev_db)
docker compose --env-file backend/.env.prod \
  -f backend/docker-compose.yml -f backend/docker-compose.prod.yml logs db-init

# Перевірити, що на свіжій БД 0 dev-тенантів і 0 dev-користувачів (але системні ролі з міграцій є)
docker compose --env-file backend/.env.prod \
  -f backend/docker-compose.yml -f backend/docker-compose.prod.yml exec -T postgres \
  psql -U manta_user -d manta_db -c \
  "SELECT (SELECT count(*) FROM tenants) AS tenants, (SELECT count(*) FROM users) AS users, (SELECT count(*) FROM roles) AS roles;"
```
Очікується: `tenants = 0`, `users = 0`, `roles = 7`.

### 8.4. Перевірка закритих портів
```bash
# На хості не повинні слухатися 5432, 6432, 6379, 8000, 8080, 1025, 8025 на 0.0.0.0
ss -tulpn | grep -E ':(5432|6432|6379|8000|8080|1025|8025)\b' || echo "OK: internal ports not exposed"
```

---

## 9. Якщо ви оновлюєте вже існуюче середовище (не з нуля)

1. **Keycloak `--import-realm` пропускає існуючий реалм:**
   - Якщо у БД `keycloak` уже існує реалм `manta` (створений раніше зі старого dev `realm-export.json`), Keycloak при рестарті **не перезапише** його автоматично.
   - Для чистого переходу: або очистіть базу `keycloak` (якщо там лише тестові дані) і перезапустіть `keycloak`, або вручну в Admin Console оновіть `sslRequired=external`, таймаути (`900s`/`1800s`/`36000s`), redirect/post-logout URIs (`https://manta.<domain>/*`), `webOrigins` та видаліть усіх 13 dev-користувачів.
2. **Ротація `ENCRYPTION_MASTER_KEY` без втрати існуючих інтеграцій:**
   - Якщо в таблиці `integrations` або `projects` вже є секрети, зашифровані старим ключем, задайте `ENCRYPTION_KEYRING_JSON` для плавної ротації:
     ```env
     ACTIVE_ENCRYPTION_KEY_ID=v2
     ENCRYPTION_KEYRING_JSON={"v2":"<NEW_64_HEX_KEY>","primary":"<OLD_64_HEX_KEY>"}
     ENCRYPTION_MASTER_KEY=<NEW_64_HEX_KEY>
     ```
   - `app` і `worker` читатимуть старі записи ключем `primary`, а всі нові шифруватимуть ключем `v2`.

---

## 10. Локальна розробка та CI (без змін)

Для звичайної локальної розробки нічого не змінилося — використовуйте базовий `backend/docker-compose.yml` без prod-оверлею:

```bash
cd backend
docker compose up -d
```

Для запуску тестів продакшн-харденінгу локально:

```bash
pytest backend/tests/test_prod_hardening.py backend/tests/test_keycloak_realm.py backend/tests/test_seed_dev_db.py backend/tests/test_pgbouncer_config.py -v
```

---

## 11. Типові проблеми та діагностика (Troubleshooting)

| Симптом | Причина | Як виправити |
| :--- | :--- | :--- |
| `app` падає на старті з `Production security configuration check failed` | Один із секретів досі має dev-значення (`admin`, `0123456789abcdef...`, `eng_app_pass_change_in_prod`), містить `localhost`/`http://`, або увімкнено `SEED_DEV_DATA=true`. | Прочитати перелік порушень у `docker compose logs app` та виправити відповідні змінні в `backend/.env.prod`. |
| `db-init` падає з `Refusing to run production migrations with SEED_DEV_DATA enabled` | Передано `SEED_DEV_DATA=true` у prod compose. | Задати `SEED_DEV_DATA=false` у `backend/.env.prod`. |
| `keycloak-realm-init` падає з `MANTA_DOMAIN is not a valid FQDN` | У `MANTA_DOMAIN` передано схему (`https://...`), `localhost` або wildcard. | Задати чистий домен, наприклад `MANTA_DOMAIN=example.com`. |
| `app` отримує `password authentication failed for user "eng_app_user"` через PgBouncer | У `DATABASE_URL` вказано пароль, що не збігається з `ENG_APP_DB_PASSWORD`, або `db-init` не відпрацював після зміни пароля. | Перевірити збіг `ENG_APP_DB_PASSWORD` і пароля в `DATABASE_URL`, перезапустити `db-init` (`docker compose ... run --rm db-init`). |
| `docker compose config` видає помилку парсингу `!reset` | На хості застарілий Docker Compose `< 2.24`. | Оновити пакет `docker-compose-plugin` до v2.24+. |
| Keycloak редіректить на `http://` замість `https://` | Caddy не передає `X-Forwarded-Proto: https` або не задано `KC_PROXY_HEADERS=xforwarded`. | Перевірити конфіг `reverse_proxy` у Caddy та змінні `KC_PROXY_HEADERS=xforwarded`, `KC_HOSTNAME_STRICT_HTTPS=true`. |
