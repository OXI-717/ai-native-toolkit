"use client";

import { useEffect, useState } from "react";

export default function NotesList({ userId }: { userId: string }) {
  const [notes, setNotes] = useState<any[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch(`/api/notes?user_id=${userId}`)
      .then((r) => r.json())
      .then(setNotes)
      .catch((e) => setError(e.message));
  }, [userId]);

  const deleteNote = async (id: string) => {
    await fetch(`/api/notes?id=${id}`, { method: "DELETE" });
    setNotes(notes.filter((n) => n.id !== id));
  };

  // VULNERABILITY: renders user-supplied HTML without sanitization
  // In production, use DOMPurify or similar
  const renderContent = (html: string) => {
    return { __html: html };
  };

  return (
    <div>
      {error && <p style={{ color: "red" }}>{error}</p>}
      {notes.map((note) => (
        <div key={note.id} className="note-card">
          <h3>{note.title}</h3>
          <div dangerouslySetInnerHTML={renderContent(note.content)} />
          <button onClick={() => deleteNote(note.id)}>Delete</button>
        </div>
      ))}
    </div>
  );
}
