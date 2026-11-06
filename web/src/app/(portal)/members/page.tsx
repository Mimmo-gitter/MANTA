import { redirect } from "next/navigation";

export default function MembersRedirectPage() {
  redirect("/access-roles");
}
