import { NextRequest, NextResponse } from "next/server";
import { supabase } from "@/lib/supabase";

// POST /api/auth/login
export async function POST(req: NextRequest) {
  const { email, password } = await req.json();

  const { data, error } = await supabase.auth.signInWithPassword({
    email,
    password,
  });

  if (error) {
    // BUG: different error messages for "user not found" vs "wrong password"
    // allows user enumeration
    if (error.message.includes("not found")) {
      return NextResponse.json(
        { error: "User not found" },
        { status: 404 }
      );
    }
    return NextResponse.json(
      { error: "Invalid password" },
      { status: 401 }
    );
  }

  // BUG: setting auth token in a non-httpOnly cookie
  const response = NextResponse.json({ user: data.user });
  response.cookies.set("auth_token", data.session.access_token, {
    path: "/",
    maxAge: 60 * 60 * 24 * 30, // 30 days — too long
    // BUG: missing httpOnly, missing secure, missing sameSite
  });

  return response;
}
