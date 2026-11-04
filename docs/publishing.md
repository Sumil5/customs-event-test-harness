# Publishing to GitLab and GitHub

The recommended setup is **GitLab as the primary** repository, because the CI pipeline and Pages live there, **push-mirrored to GitHub** so the project also shows on the GitHub profile. GitHub Actions runs the mirrored workflow too.

## 1. Create the empty projects

- **GitLab:** go to https://gitlab.com/projects/new, choose **Create blank project**, and name it `customs-event-test-harness`. Set visibility to **Public** and untick "Initialize repository with a README".
- **GitHub:** go to https://github.com/new and create `customs-event-test-harness` with **no** README, `.gitignore` or licence.

## 2. Push

From the project folder:

```bash
git remote add origin git@gitlab.com:sd00154/customs-event-test-harness.git
```

```bash
git push -u origin main
```

```bash
git remote add github git@github.com:Sumil5/customs-event-test-harness.git
```

```bash
git push -u github main
```

If you use HTTPS rather than SSH, use the `https://` URLs and a personal access token as the password.

## 3. Keep GitHub in sync automatically (optional)

In GitLab, go to **Settings → Repository → Mirroring repositories** and set:

- **Git repository URL:** `https://github.com/Sumil5/customs-event-test-harness.git`
- **Mirror direction:** Push
- **Authentication:** password, using a GitHub fine-grained token with *Contents: read and write* on that repository

After that, every push to GitLab updates GitHub, and you can drop the `github` remote.

## 4. CI notes

- **GitLab shared runners.** New GitLab.com accounts may need to verify their identity before shared runners will pick up jobs. If the pipeline sits in *pending*, check **Settings → CI/CD → Runners**. Expect a full pipeline to use roughly 15–20 compute minutes, because the two Docker jobs each pull the Oracle image. The free tier includes 400 a month, so avoid pushing lots of small commits.
- **GitLab Pages.** After the first successful pipeline on `main`, the latest reports are published. The URL is shown under **Deploy → Pages**. It is either `https://sd00154.gitlab.io/customs-event-test-harness/` or a unique-domain URL, depending on the project's Pages settings.
- **GitHub Actions.** Public repositories get free minutes. The badge in the README goes green after the first run.
- **No secrets needed.** Everything runs against throwaway containers with local development passwords.
