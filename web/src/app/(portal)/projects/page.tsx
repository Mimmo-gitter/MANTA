import { Suspense } from "react";
import { ProjectsDirectory } from "@/components/projects/projects-directory";

export default function ProjectsPage() {
  return (
    <Suspense fallback={null}>
      <ProjectsDirectory />
    </Suspense>
  );
}
