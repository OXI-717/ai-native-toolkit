import NotesList from "@/components/NotesList";
import UserSearch from "@/components/UserSearch";

export default function Home() {
  // BUG: hardcoded user ID, no session management
  const currentUserId = "user-123";

  return (
    <main style={{ maxWidth: 800, margin: "0 auto", padding: 20 }}>
      <h1>My Notes</h1>
      <UserSearch />
      <NotesList userId={currentUserId} />
    </main>
  );
}
