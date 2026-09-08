# Three-surface docs — Phase 2 publish runbook

Run these AFTER the pipeline is merged to `main`. Each step is outward-facing;
get explicit go-ahead before running it.

## 1. Enable repo features
- Settings → Features → **Wikis: ON** (else pushes 403 with a misleading auth error).
- Settings → Pages → Source: **GitHub Actions**.

## 2. Deploy key + secret (wiki push)
```bash
ssh-keygen -t ed25519 -f /tmp/wiki-key -N "" -C "aws-tui-wiki-sync"
gh repo deploy-key add /tmp/wiki-key.pub --title "aws-tui wiki sync (CI)" --allow-write
gh secret set WIKI_DEPLOY_KEY < /tmp/wiki-key          # secret holds the key CONTENT
# local verify (optional) before deleting:
WIKI_DEPLOY_KEY=/tmp/wiki-key uv run python -m scripts.docs.build_docs --wiki
WIKI_DEPLOY_KEY=/tmp/wiki-key uv run python -m scripts.docs.push_wiki --push
rm /tmp/wiki-key /tmp/wiki-key.pub
```

## 3. First publish
- Merge `develop → main`. `pages.yml` builds + deploys Pages, then the `wiki`
  job pushes `generated/wiki/` to `aws-tui.wiki.git` (**master**).

## 4. Verify all three surfaces
```bash
curl -sSfo /dev/null -w '%{http_code}\n' https://thekaveh.github.io/aws-tui/      # 200
curl -sSfo /dev/null -w '%{http_code}\n' https://github.com/thekaveh/aws-tui/wiki  # 200
# in-repo: browse docs/*.md on GitHub (canonical, always current)
```
