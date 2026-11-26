import { NextResponse, type NextRequest } from "next/server";
import { updateSession } from "@/lib/supabase/middleware";

export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  if (
    pathname === "/local-inference" ||
    pathname.startsWith("/local-inference/") ||
    pathname === "/community-nodes" ||
    pathname.startsWith("/community-nodes/")
  ) {
    return NextResponse.redirect(new URL("/chat", request.url));
  }
  return await updateSession(request);
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
  ],
};
