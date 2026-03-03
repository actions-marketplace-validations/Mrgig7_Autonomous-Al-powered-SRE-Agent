# Publishing SRE Agent to GitHub Marketplace

## Step 1: Publish GitHub Action to Marketplace

Your `action.yml` is ready. To publish:

### 1.1 Create a Release

1. Go to: https://github.com/Mrgig7/Autonomous-AI-powered-SRE-Agent/releases
2. Click **"Create a new release"**
3. Create a new tag: `v1.0.0`
4. Title: `SRE Agent v1.0.0`
5. Description:
   ```
   🚀 Initial release of SRE Agent GitHub Action
   
   Automatically analyze CI/CD failures and suggest fixes.
   
   ## Usage
   ```yaml
   - uses: Mrgig7/Autonomous-AI-powered-SRE-Agent@v1.0.0
     with:
       github-token: ${{ secrets.GITHUB_TOKEN }}
   ```
   ```
6. Check: **✅ Publish this Action to the GitHub Marketplace**
7. Select categories: `Continuous integration`, `Code quality`
8. Click **"Publish release"**

---

## Step 2: Create GitHub App (One-Click Install)

### 2.1 Create the App

1. Go to: https://github.com/settings/apps/new
2. Fill in:
   - **Name:** `SRE Agent`
   - **Homepage URL:** `https://github.com/Mrgig7/Autonomous-AI-powered-SRE-Agent`
   - **Webhook URL:** `https://your-server.com/webhooks/github` (or use smee.io for testing)
   - **Webhook secret:** Create a secure random string

### 2.2 Set Permissions

**Repository permissions:**
- Actions: Read
- Checks: Read & Write
- Contents: Read
- Issues: Read & Write
- Pull requests: Read & Write
- Workflows: Read

**Events to subscribe:**
- ✅ Workflow run
- ✅ Workflow job
- ✅ Check run

### 2.3 Generate Private Key

1. After creating, scroll to **"Private keys"**
2. Click **"Generate a private key"**
3. Save the `.pem` file securely

### 2.4 Install the App

1. Go to your app's page
2. Click **"Install App"**
3. Select repositories

---

## Step 3: (Optional) Use Smee.io for Local Testing

If you don't have a public server:

```bash
# Install smee client
npm install -g smee-client

# Create a channel at https://smee.io
# Then run:
smee -u https://smee.io/your-channel -t http://localhost:8000/webhooks/github
```

---

## Your Action is Now Live! 🎉

Users can use it by adding:
```yaml
- uses: Mrgig7/Autonomous-AI-powered-SRE-Agent@v1.0.0
```
