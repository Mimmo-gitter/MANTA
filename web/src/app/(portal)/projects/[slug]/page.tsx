"use client";

import { useEffect } from "react";
import { useParams, useRouter } from "next/navigation";

export default function ProjectSlugIndexPage() {
  const params = useParams<{ slug: string }>();
  const router = useRouter();
  const slug = params?.slug ?? "";

  useEffect(() => {
    if (slug) {
      router.replace(`/projects/${slug}/settings`);
    } else {
      router.replace("/projects");
    }
  }, [router, slug]);

  return null;
}
