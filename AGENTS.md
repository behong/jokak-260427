# Repository Instructions

## Longform-only YouTube workflow

- Apply the rules in `docs/longform-youtube-upload-workflow.md` only when a task concerns longform video creation, longform YouTube upload preparation, scheduling, or post-publish review.
- Do not apply those rules to Shorts, Naver Clip, ordinary dashboard work, or unrelated development tasks.
- When a matching file exists under `docs/longform-upload-jobs/`, treat that per-video file as the source of truth for its title candidates, description, tags, playlist, schedule, and post-publish actions.
- Never generate, edit, crop, or substitute a longform thumbnail. Use only the three 16:9 images supplied by the user, in the requested order.
- If an upload setting cannot be applied through the YouTube API or the dashboard, prepare it as an explicit manual checklist instead of silently omitting it.

