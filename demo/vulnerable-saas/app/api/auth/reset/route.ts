import { NextRequest, NextResponse } from "next/server";
import { supabaseAdmin } from "@/lib/supabase";

// POST /api/auth/reset
// BUG: no rate limiting on password reset — allows email bombing
export async function POST(req: NextRequest) {
  const { email } = await req.json();

  // BUG: confirms whether email exists before sending reset
  const { data: user } = await supabaseAdmin
    .from("profiles")
    .select("id")
    .eq("email", email)
    .single();

  if (!user) {
    return NextResponse.json(
      { error: "No account with this email" },
      { status: 404 }
    );
  }

  const { error } = await supabase.auth.resetPasswordForEmail(email, {
    redirectTo: `${req.nextUrl.origin}/reset-password`,
  });

  if (error) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }

  return NextResponse.json({ success: true });
}

// Need to import supabase for the reset call
import { supabase } from "@/lib/supabase";
