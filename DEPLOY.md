# Deploying on Streamlit Community Cloud, with the data in a private Hugging Face dataset

The code lives in the public GitHub repo. The data (the database, about 1.6 GB once indexed) cannot live in GitHub, and it should not be public, so it sits in a **private Hugging Face dataset**.
On first start the app downloads a compressed copy (about 140 MB), checks its checksum, unpacks it and builds the indexes (about 15 seconds on a laptop; allow a minute or two on the host).

```
your Mac:   make db-package  ->  make db-publish  ->  private Hugging Face dataset  <-  the app fetches it on first start (READ token)  <-  Streamlit Community Cloud
```

Nothing secret is ever committed: the tokens and the AI key (and the access code, if you use one) go into Streamlit's **Secrets** box (`.streamlit/secrets.toml.example` is the template; the real `secrets.toml` is git-ignored).

## 1. One-time setup on Hugging Face (you)
1. Create a free account at huggingface.co. Your username below is `<hf-name>`.
2. Create the dataset: **New > Dataset**, name `nsw-demo-db`, visibility **Private**.
3. Create two **fine-grained** access tokens (Settings > Access Tokens > New token), each limited to that one dataset:
   - `nsw-publish`: write access. It stays on your Mac and is used only to upload.
   - `nsw-read`: read access only. This is the one that goes into Streamlit's Secrets.

## 2. Publish the data (from this repo, on your Mac)
```
make db-unindexed        # only if the shared data has changed since the last time
make db-package          # compresses data/nsw_unindexed.db (+ the AI answer cache and capabilities) into data/hf_upload/
read -s HF_TOKEN && export HF_TOKEN            # paste the WRITE token; it is not shown and not saved in your shell history
NSW_DB_REPO=<hf-name>/nsw-demo-db make db-publish
unset HF_TOKEN
```
The upload refuses to go to a public dataset. The package includes the AI answer cache, so the landing page's first question and suggestions answer instantly on the new host.

## 3. Deploy on Streamlit Community Cloud
1. Open https://share.streamlit.io and sign in with the GitHub account that owns the repo.
2. **Create app**: repository `analytic_poc`, branch `main`, main file `app/main.py`.
3. **Advanced settings**: Python 3.12. Paste the Secrets box into **Secrets** (the AI key, `NSW_VIEW_ONLY`, the token cap, `NSW_DB_REPO` and the READ token; the access code is optional). Fill in the commented block at the bottom of your `.env`, then run `make streamlit-secrets` to print the box ready to paste (`ARGS=--mask` previews it with secrets hidden, `ARGS=--copy` puts it on the clipboard); it refuses while anything is missing or still a placeholder. `.streamlit/secrets.toml.example` shows the same layout.
4. Deploy. The first start shows "Getting the data ready" for a minute or two, then the landing page (or the access-code prompt, if you set a code).
5. Check: ask one of the suggestions on the landing page, open the three sections.

## 4. Looking after it
- **New data:** publish again (step 2), then in the app's Secrets add `NSW_DB_FORCE = "1"`, reboot the app, and remove the line afterwards. Without it a host that still has an older copy keeps using it.
- **New code:** push to GitHub; Community Cloud redeploys by itself.
- **Sleeping:** apps with no visitors for 12 hours sleep; the next visit wakes them, and if the disk was cleared the data is fetched again (a minute or two).
- **"Over its resource limits" or restarts:** the free tier gives an app 690 MB to 2.7 GB of memory and this app peaks at about 1.7 GB. If it keeps happening, reboot it first; if it persists, a host with more memory is the answer (see below).
- **Checksum or "different version" messages:** publish again from the same version of the code, then reboot.
- **AI unavailable:** check `OPENAI_API_KEY` and `OPENAI_BASE_URL` in Secrets.

## 5. What is public and what is not
| Public (GitHub) | Private (Hugging Face, Streamlit Secrets, your Mac) |
|---|---|
| the code, docs and tests | the database and the AI answer cache (private dataset) |
| the secrets template (no values) | the READ token, the AI key and the optional access code (Secrets box) |
| | the WRITE token (your Mac only) |

The Streamlit app's address is reachable by anyone who has it. **Without an access code (leave `NSW_ACCESS_CODE` out of the Secrets box) there is no prompt at all**: anyone with the address can read the demo data and use the AI, and view-only mode plus the daily token cap are what limit them. To take the prompt away on a deployed app, delete the `NSW_ACCESS_CODE` line in the Secrets box on Streamlit (App settings > Secrets) and save: the app restarts without it. To ask for a code again, add the line back.

## If the free tier is not enough
The data package works on any host that can run Streamlit and set environment variables: set `NSW_DB_REPO`, `HF_TOKEN` and the other settings from the template as environment variables and start `streamlit run app/main.py`.
