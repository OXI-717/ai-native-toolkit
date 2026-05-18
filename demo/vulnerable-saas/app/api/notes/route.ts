import { NextRequest, NextResponse } from "next/server";
import { supabaseAdmin } from "@/lib/supabase";

// GET /api/notes?user_id=xxx
// BUG: IDOR — any user can read any other user's notes by changing user_id
export async function GET(req: NextRequest) {
  const userId = req.nextUrl.searchParams.get("user_id");

  const { data, error } = await supabaseAdmin
    .from("notes")
    .select("*")
    .eq("user_id", userId);

  if (error) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }

  return NextResponse.json(data);
}

// POST /api/notes
// BUG: no input validation, no size limit, no auth check
export async function POST(req: NextRequest) {
  const body = await req.json();

  const { data, error } = await supabaseAdmin
    .from("notes")
    .insert({
      user_id: body.user_id,
      title: body.title,
      content: body.content,
      is_public: body.is_public ?? false,
    })
    .select()
    .single();

  if (error) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }

  return NextResponse.json(data);
}

// DELETE /api/notes?id=xxx
// BUG: no ownership check — any user can delete any note
export async function DELETE(req: NextRequest) {
  const noteId = req.nextUrl.searchParams.get("id");

  const { error } = await supabaseAdmin
    .from("notes")
    .delete()
    .eq("id", noteId);

  if (error) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }

  return NextResponse.json({ success: true });
}
