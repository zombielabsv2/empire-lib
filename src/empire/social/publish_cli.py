"""Generic empire publishing CLI — post anything to any connected brand.

The same tool every brand uses (Iqbal, Moonpath, Lotus Lane, ad-hoc KBK).
A brand's pipeline renders images/video locally; this stages them and
creates a Metricool post.

    python -m empire.social.publish_cli \\
        --brand "Iqbal" \\
        --media slide1.jpg slide2.jpg \\
        --caption-file caption.txt \\
        --when "2026-05-20 18:00" \\
        --networks instagram,facebook

Default is a DRAFT (nothing publishes; a human approves it in Metricool).
Pass --live for a hands-off scheduled post.

Credentials are read from the environment:
    METRICOOL_USER_TOKEN, METRICOOL_USER_ID
(canonical copy: GCP Secret Manager, project kbk-cron).
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from empire.social.metricool import MetricoolClient
from empire.social.staging import stage_many


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="empire.social.publish_cli",
                                 description="Publish to a Metricool brand")
    ap.add_argument("--brand", required=True,
                    help="brand name to match (e.g. 'Iqbal'), or a numeric blogId")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--media", nargs="+", help="media files, in order")
    g.add_argument("--folder", help="folder of images (sorted by name)")
    ap.add_argument("--caption", help="caption text")
    ap.add_argument("--caption-file", help="caption file path")
    ap.add_argument("--first-comment", default="", help="optional first comment")
    ap.add_argument("--when", required=True, help='"YYYY-MM-DD HH:MM" (IST)')
    ap.add_argument("--networks", default="instagram", help="comma list")
    ap.add_argument("--type", default="post", choices=["post", "reel", "story"])
    ap.add_argument("--prefix", default="empire", help="staging folder prefix")
    ap.add_argument("--live", action="store_true",
                    help="hands-off scheduled post (default: draft for review)")
    args = ap.parse_args(argv)

    try:
        when = datetime.strptime(args.when, "%Y-%m-%d %H:%M")
    except ValueError:
        print('ERROR: --when must be "YYYY-MM-DD HH:MM"', file=sys.stderr)
        return 2

    if args.folder:
        folder = Path(args.folder)
        media_paths = sorted(
            str(p) for p in folder.iterdir()
            if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".mp4")
        )
    else:
        media_paths = args.media
    if not media_paths:
        print("ERROR: no media found", file=sys.stderr)
        return 2

    caption = args.caption or ""
    if args.caption_file:
        caption = Path(args.caption_file).read_text(encoding="utf-8").strip()
    if not caption:
        print("ERROR: provide --caption or --caption-file", file=sys.stderr)
        return 2

    networks = [n.strip() for n in args.networks.split(",") if n.strip()]
    client = MetricoolClient()

    if args.brand.isdigit():
        blog_id: int | str = int(args.brand)
    else:
        brand = client.find_brand(args.brand)
        if not brand:
            avail = [(b["id"], b.get("label")) for b in client.list_brands()]
            print(f"ERROR: no brand matching {args.brand!r}. Available: {avail}",
                  file=sys.stderr)
            return 1
        blog_id = brand["id"]

    print(f"Brand {blog_id} | {len(media_paths)} media | {args.type} | "
          f"{'LIVE' if args.live else 'DRAFT'} | {when:%Y-%m-%d %H:%M}")
    media = stage_many(media_paths, prefix=args.prefix)
    res = client.schedule_post(
        blog_id=blog_id, text=caption, networks=networks, publish_at=when,
        media=media, post_type=args.type, draft=not args.live,
        autopublish=args.live, first_comment=args.first_comment,
    )
    print(f"DONE — post id {res.post_id} "
          f"({'scheduled' if args.live else 'draft, awaiting review'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
