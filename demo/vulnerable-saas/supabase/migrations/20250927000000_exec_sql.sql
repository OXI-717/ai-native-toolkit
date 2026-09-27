-- INTENTIONALLY VULNERABLE — teaching fixture for the demo app.
-- This function executes arbitrary SQL passed as a string parameter.
-- It exists so that app/api/users/route.ts has a real SQL-injection sink
-- (string-concatenated user input -> exec_sql RPC). NEVER ship anything
-- like this in a real application.

create or replace function exec_sql(sql text)
returns setof json
language plpgsql
security definer -- BUG: runs with the privileges of the function owner
as $$
begin
  -- BUG: caller-built SQL is spliced in verbatim; rows are wrapped as json so
  -- any SELECT (e.g. SELECT * FROM profiles ...) matches the return type.
  return query execute 'select to_json(t) from (' || sql || ') t';
end;
$$;
