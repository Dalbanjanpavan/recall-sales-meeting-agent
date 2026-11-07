# Repository Review Before Upload

## Review completed

- The application folders are small and easy to follow: Python services at the root, `templates/` and `static/` for the web interface, and `tests/` for regression coverage.
- The existing root `README.md`, `PROJECT_ARCHITECTURE.md`, and `ENGINEERING_HANDOVER.md` were read before preparing these materials.
- The application files were not modified. All submission files are in `submission_materials/`.
- `.gitignore` excludes `.env`, `.venv/`, Python caches, `*.pyc`, `*.db`, and `*.db-journal`.
- No credentials or database files are included in the submission materials.
- The project does not currently contain a checked-in `requirements.txt` or dependency lock file. The submission README calls this out instead of hiding it.

## Current verification status

The existing documentation records 69 tests passing in an earlier run. I reran the suite during this review: all 69 tests executed, but 3 date-conflict tests failed. They use a bare “by Friday” example and expect the reference date to be Friday, 2026-11-06. The current configured date is later, so the date parser resolves that phrase differently. This should be fixed in the application test suite before claiming a fresh green run.

The Python compilation check and frontend JavaScript syntax check were not reached after the test command stopped on the test failure in the combined validation command; they should be run separately after the date-sensitive tests are corrected. Live Groq and Hindsight behavior remains unverified in this environment.

## Files to keep private

Keep these local and out of a public repository:

- `.env`
- `recall.db` and any other SQLite database files
- `.venv/`
- customer notes or exported memories containing private data
- API keys in logs, screenshots, or issue reports

## Suggested upload commands

Run these from the project root after reviewing the diff and choosing the correct repository visibility:

```powershell
git status
git add .
git diff --cached --check
git commit -m "Prepare Recall submission materials"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git
git push -u origin main
```

If `origin` already exists, inspect it first with `git remote -v`; do not add a second remote with the same name. Before pushing, use `git diff --cached --name-only` and confirm that `.env`, databases, and private data are absent.

## Personal items still needed

- Replace `[PASTE_PUBLIC_ARTICLE_URL]` in `linkedin_post.md` and `reddit_post.md` after publishing the article.
- The guide recommends screenshots or diagrams in the article. Use synthetic data only; the article now lists safe visual options.
- Decide whether the repository should be public for the submission.
- Correct or freeze the date-sensitive tests, then rerun the full suite.
- Run a live check separately if credentials are available, and report the result honestly.
