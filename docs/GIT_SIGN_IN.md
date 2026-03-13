# Sign in when you push to GitHub

The remote is set to **HTTPS** (`https://github.com/michaelsam94/intelleigent-trading-bot.git`), so when you run `git push`, Git will ask you to sign in if it doesn’t have valid credentials.

## What happens when you push

1. Run: `git push -u origin master` (or `git push` after the first time).
2. If Git needs credentials, it will prompt you:
   - **Terminal:** it may ask for your GitHub username and password.
   - **macOS Keychain:** a popup may appear asking to allow access or enter your password.
   - **Git Credential Manager (if installed):** a browser window may open to sign in with your GitHub account.

3. Use your **michaelsam94** GitHub account:
   - **Username:** `michaelsam94`
   - **Password:** use a **Personal Access Token (PAT)**, not your GitHub password.  
     Create one: GitHub → Settings → Developer settings → Personal access tokens → Generate new token (classic). Give it `repo` scope.

## Get a browser “Sign in with GitHub” popup

If you want a browser popup to sign in with GitHub when you push:

1. Install **Git Credential Manager** (GCM):
   - **macOS (Homebrew):** `brew install git-credential-manager`
   - Or: https://github.com/git-ecosystem/git-credential-manager#download

2. Configure Git to use it (if not automatic):
   ```bash
   git config --global credential.helper manager
   ```

3. The next time you run `git push`, GCM can open a browser so you can sign in with your **michaelsam94** account.

## Cached credentials were cleared

Stored GitHub credentials for this machine were cleared so that the next push will ask you to sign in. After you sign in once, your system (e.g. Keychain or GCM) can store it for future pushes.
