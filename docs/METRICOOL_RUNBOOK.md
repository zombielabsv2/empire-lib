# Metricool — Empire Social Publishing Runbook

Status as of 2026-05-16. Account: `kriti629@gmail.com`, Metricool Advanced.

## What is already built and tested (autonomous work, done)

- **`empire.social.metricool`** — shared Metricool API client (auth, brands,
  media normalize, scheduled posts). Tested live: created and deleted a real
  draft post against the account.
- **`empire.social.staging`** — uploads local media to a public GCS bucket
  (`rxj-metricool-staging`, objects auto-delete after 2 days) so Metricool can
  fetch it. Tested live with real KBK carousel slides.
- **`empire.social.publish_cli`** — generic CLI: post any media to any brand.
- **`kari-growth-platform/scripts/publish_carousel_to_metricool.py`** — KBK
  carousel → Metricool draft. Tested live end-to-end with the ikkats carousel.
- Credentials stored in **GCP Secret Manager** (`kbk-cron`):
  `METRICOOL_USER_TOKEN`, `METRICOOL_USER_ID`. Also in KBK `.streamlit/secrets.toml`.

The code is verified. **Publishing cannot go live until the one human step
below is done — no script can do it.**

## THE ONE HUMAN STEP: connect each brand's accounts (OAuth)

Connecting a social account to Metricool is an OAuth login — a person must log
into that account and click "authorize". This cannot be automated, and it is
the *only* thing standing between the built pipelines and live posting. Budget
about 10 minutes per brand.

### How to connect a brand (do this in app.metricool.com)

1. Log in as `kriti629@gmail.com`.
2. Brand selector (top-left) → **Add brand** (or rename the existing empty
   brand). Name it clearly, e.g. `Kari by Kriti`.
3. For each network, click **Connect**, log into that account, authorize
   Metricool. For Instagram: choose **connect via Facebook** (more stable). The
   Instagram account must be a **Business or Creator** account linked to a
   **Facebook Page** — convert it in the Instagram app first if needed.
4. Repeat per brand.

### Per-brand checklist

| Brand | Accounts to connect | Account status / action needed |
|-------|--------------------|--------------------------------|
| **Kari by Kriti** | Instagram, Facebook Page, Pinterest, Google Business | Instagram + FB Page already exist (KBK runs FB ads). Pinterest is new — create `@karibykriti` on Pinterest; it is a strong channel for home decor. **Do this brand first** — its content pipeline is ready. |
| **Iqbal for All** | Instagram, Facebook Page | Almost certainly need to be **created** (`iqbalforall.org` just launched). Create `@iqbalforall` as a Business account + a Facebook Page, then connect. |
| **Moonpath** | Instagram | Likely needs to be **created**. Decide first whether Moonpath wants an organic Instagram presence; if yes, create `@moonpath` (Business) and connect. |
| **Lotus Lane** | Instagram | Lotus Lane already has a podcast + newsletter. Connect its Instagram if one exists; if not, decide whether Instagram is worth opening for it at all. |

Honest note: only **KBK** has accounts that exist today. Iqbal, Moonpath and
Lotus Lane need their Instagram accounts created first — that is account-setup
work, not something the pipeline can skip.

## After connecting: how to publish

Both tools create a **draft** by default — nothing posts until a human approves
it in Metricool (Planning tab). Add `--live` only for hands-off scheduling.

Set credentials once per shell:

```
export METRICOOL_USER_TOKEN=$(gcloud secrets versions access latest --secret=METRICOOL_USER_TOKEN --project=kbk-cron)
export METRICOOL_USER_ID=$(gcloud secrets versions access latest --secret=METRICOOL_USER_ID --project=kbk-cron)
```

**KBK carousel:**
```
cd ~/kari-growth-platform
python scripts/publish_carousel_to_metricool.py \
    data/instagram/carousels/<folder> \
    --caption-file caption.txt --when "2026-05-20 11:00"
```

**Any other brand (generic):**
```
PYTHONPATH=~/empire-lib/src python -m empire.social.publish_cli \
    --brand "Iqbal" --folder ./images \
    --caption-file caption.txt --when "2026-05-20 18:00" \
    --networks instagram,facebook
```

## What still needs building (content pipelines, per brand)

The publishing layer is universal and done. What each brand still needs is a
*content* pipeline that produces the images/captions:

- **KBK** — done (`build_kbk_carousel.py` → `publish_carousel_to_metricool.py`).
- **Iqbal for All** — a couplet-to-card generator (a verse → a designed card).
  Same shape as the AstroMedha card factory. Not yet built.
- **Moonpath** — an article-to-card or quote-card pipeline. Not yet built.
- **Lotus Lane** — could repurpose podcast episodes into audiogram clips. Not
  yet built.

Until those exist, any brand can still publish today by pointing the generic
CLI at hand-made images.

## Hard limits (platform rules, not Metricool's — apply to every tool)

- Instagram Stories with a tappable **link sticker** cannot be auto-published.
- Instagram Reels cannot use Instagram's **trending audio** when auto-published
  (use baked-in audio, or finish that one Reel by hand).
- These are why `autopublish=False` / `draft=True` exists in the client.
