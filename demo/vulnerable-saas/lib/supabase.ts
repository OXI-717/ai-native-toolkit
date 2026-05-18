import { createClient } from "@supabase/supabase-js";

// BUG: hardcoded credentials instead of environment variables
const supabaseUrl = "https://xyzcompany.supabase.co";
const supabaseAnonKey = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inh5emNvbXBhbnkiLCJyb2xlIjoiYW5vbiIsImlhdCI6MTcxNjAwMDAwMCwiZXhwIjoyMDMxNTc2MDAwfQ.fake-demo-key-not-real";
const supabaseServiceKey = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inh5emNvbXBhbnkiLCJyb2xlIjoic2VydmljZV9yb2xlIiwiaWF0IjoxNzE2MDAwMDAwLCJleHAiOjIwMzE1NzYwMDB9.fake-service-key-not-real";

export const supabase = createClient(supabaseUrl, supabaseAnonKey);

// BUG: service role client exposed — should never be used in client-side code
export const supabaseAdmin = createClient(supabaseUrl, supabaseServiceKey);
