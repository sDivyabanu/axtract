import { redirect } from "next/navigation";

// Old bookmarks return to the landing page; there is no account flow.
export default function LoginPage() {
  redirect("/");
}
