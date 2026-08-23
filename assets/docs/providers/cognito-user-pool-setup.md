# AWS Cognito User Pool Setup Guide

This guide explains how to set up an AWS Cognito User Pool for use with Claude Code authentication. The User Pool can be used standalone or integrated with external OIDC identity providers like Okta, Auth0, or Microsoft Entra ID.

## Overview

The CloudFormation template creates a Cognito User Pool with:
- OAuth2 Authorization Code flow
- Proper token validity settings
- Support for external OIDC providers
- Pre-configured attribute mappings

## Prerequisites

- AWS CLI configured with appropriate credentials
- Permissions to create Cognito User Pools and IAM roles
- A unique domain prefix for your Cognito domain

## Quick Start

### 1. Deploy the User Pool

```bash
# Clone the repository
git clone https://github.com/cdharma/claude-with-bedrock
cd claude-with-bedrock

# Deploy the User Pool stack
aws cloudformation deploy \
  --template-file deployment/infrastructure/cognito-user-pool-setup.yaml \
  --stack-name claude-code-user-pool \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    UserPoolName=claude-code-auth \
    DomainPrefix=my-unique-domain-prefix \
    CallbackURLs=http://localhost:8400/callback
```

### 2. Get the Configuration Values

```bash
# Get User Pool ID
aws cloudformation describe-stacks \
  --stack-name claude-code-user-pool \
  --query 'Stacks[0].Outputs[?OutputKey==`UserPoolId`].OutputValue' \
  --output text

# Get Client ID
aws cloudformation describe-stacks \
  --stack-name claude-code-user-pool \
  --query 'Stacks[0].Outputs[?OutputKey==`UserPoolClientId`].OutputValue' \
  --output text

# Get Domain
aws cloudformation describe-stacks \
  --stack-name claude-code-user-pool \
  --query 'Stacks[0].Outputs[?OutputKey==`UserPoolDomain`].OutputValue' \
  --output text
```

### 3. Configure Claude Code

The `ccwb` tool runs from the `source/` directory:

```bash
cd source
poetry install

# Initialize Claude Code with your User Pool
poetry run ccwb init

# When prompted, enter:
# - Provider Domain: <your-domain-prefix>.auth.<region>.amazoncognito.com
# - User Pool ID: <from step 2>
# - Client ID: <from step 2>
```

### 4. Deploy the Identity Pool

```bash
# Deploy the authentication infrastructure
poetry run ccwb deploy auth
```

## Configuration Options

### Basic Parameters

- `UserPoolName`: Name for your User Pool (default: claude-code-auth)
- `DomainPrefix`: Unique prefix for Cognito domain (required)
- `CallbackURLs`: OAuth2 callback URLs (default: http://localhost:8400/callback)
- `LogoutURLs`: OAuth2 logout URLs (default: http://localhost:8400/logout)

## User Pool Configuration

The template creates a User Pool with the following settings:

### Sign-in Options
- Username with email alias
- Email as required attribute
- preferred_username as required attribute

### Security Settings
- Self-registration disabled
- Password policy: 8+ chars, upper/lower/numbers/symbols
- MFA optional (can be configured per user)
- Token revocation enabled
- Prevent user existence errors enabled

### Token Validity
- Authentication flow session: 3 minutes
- Refresh token: 600 minutes (10 hours)
- Access token: 10 minutes
- ID token: 60 minutes

### OAuth2 Configuration
- Authorization code flow only
- Scopes: openid, email, profile
- No implicit grant flow

## Adding Users

Since self-registration is disabled, you need to create users manually:

### Via AWS Console
1. Navigate to Cognito > User pools > Your pool
2. Click "Create user"
3. Enter username and temporary password
4. User will need to change password on first login

### Via AWS CLI
```bash
aws cognito-idp admin-create-user \
  --user-pool-id <your-user-pool-id> \
  --username <username> \
  --user-attributes Name=email,Value=user@example.com \
  --temporary-password <temp-password>
```

## Integrating External Identity Providers

To federate the User Pool with an external OIDC provider (Okta, Auth0, Microsoft Entra ID, etc.):

1. In your provider, register an application with the redirect URI `https://<domain-prefix>.auth.<region>.amazoncognito.com/oauth2/idpresponse`
2. In the Cognito console, add the provider under your User Pool as an OpenID Connect identity provider (client ID/secret and issuer URL come from your provider)
3. Map attributes appropriately (at minimum `email`; also `given_name` and `family_name` if available)
4. Update the app client's supported identity providers to include the new provider

## Troubleshooting

### Group-Level Dashboards Show Default Values

If CloudWatch dimensions show `department: "unspecified"` or `team: "default-team"`, your Cognito tokens may not include custom attributes. Enable the Pre-Token-Generation trigger:

```bash
# Redeploy with custom claim injection enabled
aws cloudformation deploy \
  --template-file deployment/infrastructure/cognito-user-pool-setup.yaml \
  --stack-name claude-code-user-pool \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    EnableCustomClaimInjection=true
```

Then ensure users have custom attributes set:
```bash
aws cognito-idp admin-update-user-attributes \
  --user-pool-id <pool-id> \
  --username user@example.com \
  --user-attributes \
    Name=custom:department,Value=engineering \
    Name=custom:team,Value=platform
```

Users must re-authenticate for the new claims to appear in their tokens.

> **Note:** This is only needed for pure Cognito deployments. If you use an external IdP (Okta, Azure AD, Google), those providers already emit standard claims via OIDC.

### Domain Already Exists
If you get a domain conflict error, choose a different `DomainPrefix`. Cognito domains must be globally unique.

### Missing Outputs
Ensure the stack deployment completed successfully:
```bash
aws cloudformation describe-stacks \
  --stack-name claude-code-user-pool \
  --query 'Stacks[0].StackStatus'
```

### Authentication Issues
Check that:
1. User exists in the User Pool
2. Callback URL matches exactly
3. App client has correct identity providers

## Cleanup

To remove the User Pool:
```bash
aws cloudformation delete-stack --stack-name claude-code-user-pool
```

Note: This will delete all users and configurations. Back up any important data first.