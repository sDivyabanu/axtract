-- Row Level Security policies for AXTRACT application tables.
-- Run this in the Supabase SQL Editor after Prisma migrations.
--
-- NOTE: Prisma backend uses privileged credentials that bypass RLS.
-- The backend enforces ownership in all queries explicitly.
-- These policies protect against direct Supabase client access.

-- Enable RLS on all application tables
ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE processing_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_outputs ENABLE ROW LEVEL SECURITY;

-- Profiles: users can read/update their own profile
CREATE POLICY "Users can view own profile"
  ON profiles FOR SELECT
  USING (auth.uid() = id);

CREATE POLICY "Users can update own profile"
  ON profiles FOR UPDATE
  USING (auth.uid() = id);

CREATE POLICY "Users can insert own profile"
  ON profiles FOR INSERT
  WITH CHECK (auth.uid() = id);

-- Documents: users can only access their own documents
CREATE POLICY "Users can view own documents"
  ON documents FOR SELECT
  USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own documents"
  ON documents FOR INSERT
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own documents"
  ON documents FOR UPDATE
  USING (auth.uid() = user_id);

CREATE POLICY "Users can delete own documents"
  ON documents FOR DELETE
  USING (auth.uid() = user_id);

-- Document versions: access through document ownership
CREATE POLICY "Users can view own document versions"
  ON document_versions FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM documents
      WHERE documents.id = document_versions.document_id
      AND documents.user_id = auth.uid()
    )
  );

-- Processing runs: access through document ownership
CREATE POLICY "Users can view own processing runs"
  ON processing_runs FOR SELECT
  USING (auth.uid() = user_id);

-- Document outputs: access through processing run ownership
CREATE POLICY "Users can view own document outputs"
  ON document_outputs FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM processing_runs
      WHERE processing_runs.id = document_outputs.processing_run_id
      AND processing_runs.user_id = auth.uid()
    )
  );

-- Storage bucket policy (run in Supabase dashboard or SQL editor)
-- CREATE POLICY "Users can upload own documents"
--   ON storage.objects FOR INSERT
--   WITH CHECK (
--     bucket_id = 'axtract-documents'
--     AND auth.uid()::text = (string_to_array(name, '/'))[1]
--   );
--
-- CREATE POLICY "Users can read own documents"
--   ON storage.objects FOR SELECT
--   USING (
--     bucket_id = 'axtract-documents'
--     AND auth.uid()::text = (string_to_array(name, '/'))[1]
--   );
