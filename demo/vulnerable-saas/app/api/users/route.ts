import { NextRequest, NextResponse } from "next/server";
import { supabaseAdmin } from "@/lib/supabase";

// GET /api/users?search=xxx
// BUG: raw SQL query with string interpolation — SQL injection
export async function GET(req: NextRequest) {
  const search = req.nextUrl.searchParams.get("search") || "";

  const { data, error } = await supabaseAdmin.rpc("search_users", {
    query: `%${search}%`,
  });

  // BUG: leaking full user objects including email, created_at, metadata
  if (error) {
    // BUG: leaking internal error details to client
    return NextResponse.json(
      { error: error.message, details: error.details, hint: error.hint },
      { status: 500 }
    );
  }

  return NextResponse.json(data);
}

// PATCH /api/users?id=xxx
// BUG: mass assignment — accepts any fields including role, is_admin
export async function PATCH(req: NextRequest) {
  const userId = req.nextUrl.searchParams.get("id");
  const body = await req.json();

  // BUG: no field whitelist, user can set { role: "admin", is_admin: true }
  const { data, error } = await supabaseAdmin
    .from("profiles")
    .update(body)
    .eq("id", userId)
    .select()
    .single();

  if (error) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }

  return NextResponse.json(data);
}
