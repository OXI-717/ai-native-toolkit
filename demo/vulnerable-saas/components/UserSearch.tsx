"use client";

import { useState } from "react";

export default function UserSearch() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<any[]>([]);

  const search = async () => {
    const res = await fetch(`/api/users?search=${query}`);
    const data = await res.json();
    setResults(data);
  };

  // BUG: no URL encoding of query parameter — potential injection
  // BUG: displaying all user fields including email (data leak)
  return (
    <div>
      <input
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search users..."
      />
      <button onClick={search}>Search</button>
      <ul>
        {results.map((user: any) => (
          <li key={user.id}>
            {user.name} — {user.email} — role: {user.role}
          </li>
        ))}
      </ul>
    </div>
  );
}
