-- Fix: Supabase Auth signup fails with "Database error saving new user".
--
-- Root cause: handle_new_user() is SECURITY DEFINER and runs as the auth
-- role, whose search_path does not include `public`, so the unqualified
-- `profiles` reference fails at runtime. Adding `SET search_path = public`
-- pins the resolution.
--
-- HOW TO APPLY: open the Supabase dashboard -> SQL Editor, paste this whole
-- file, and run it. Then signup will work.

CREATE OR REPLACE FUNCTION handle_new_user()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    INSERT INTO profiles (id, full_name, created_at, updated_at)
    VALUES (
        NEW.id,
        COALESCE(NEW.raw_user_meta_data->>'full_name', split_part(NEW.email, '@', 1)),
        NOW(),
        NOW()
    )
    ON CONFLICT (id) DO NOTHING;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW
    EXECUTE FUNCTION handle_new_user();
