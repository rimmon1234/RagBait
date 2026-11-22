import { createServerClient } from "@supabase/ssr";
import { type NextRequest, NextResponse } from "next/server";

export async function updateSession(request: NextRequest) {
  let supabaseResponse = NextResponse.next({
    request,
  });

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL || "http://127.0.0.1:54321",
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || "mock-anon-key-for-local-dev-at-least-32-chars",
    {
      cookies: {
        getAll() {
          return request.cookies.getAll();
        },
        setAll(cookiesToSet: Array<{ name: string; value: string; options?: any }>) {
          cookiesToSet.forEach(({ name, value }) => request.cookies.set(name, value));
          supabaseResponse = NextResponse.next({
            request,
          });
          cookiesToSet.forEach(({ name, value, options }) =>
            supabaseResponse.cookies.set(name, value, options)
          );
        },
      },
    }
  );

  // Refresh auth token and inspect user session
  const {
    data: { user },
  } = await supabase.auth.getUser();

  const pathname = request.nextUrl.pathname;
  const isAuthRoute =
    pathname.startsWith("/login") ||
    pathname.startsWith("/register") ||
    pathname.startsWith("/auth");

  const isProtectedRoute =
    pathname.startsWith("/local-inference") ||
    pathname.startsWith("/credentials") ||
    pathname.startsWith("/access") ||
    pathname.startsWith("/community-nodes") ||
    pathname.startsWith("/knowledge-base") ||
    pathname.startsWith("/admin");

  if (!user && isProtectedRoute) {
    const redirectUrl = request.nextUrl.clone();
    redirectUrl.pathname = "/login";
    redirectUrl.searchParams.set("redirectedFrom", pathname);
    return NextResponse.redirect(redirectUrl);
  }

  if (user && isAuthRoute && !pathname.startsWith("/auth/callback")) {
    const redirectUrl = request.nextUrl.clone();
    redirectUrl.pathname = "/local-inference";
    redirectUrl.searchParams.delete("redirectedFrom");
    return NextResponse.redirect(redirectUrl);
  }

  return supabaseResponse;
}
