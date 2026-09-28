# Invite-only user onboarding

## Decision

New Process Memory accounts are created through administrator invitations. Cognito self-registration and automatic OPM enrollment are disabled. An authenticated company Owner invites a user from the installer. Invitations create a Cognito identity and an active company membership with the least-privileged selected role; Owner cannot be assigned from the invite form. The initial company Owner remains provisioned through the trusted bootstrap process.

## Flow

1. An operator creates the first Cognito identity through the AWS administrator path, then runs the company bootstrap with that identity's Cognito `sub` to establish the initial Owner.
2. The Owner signs into the installer with Cognito and selects the company already bound to their active membership.
3. The Owner enters an email address and selects Member, Operator, Reviewer, or Auditor.
4. The backend resolves the Owner from the verified token, rejects non-Owners, creates or locates the Cognito identity, and persists the Cognito subject plus company membership through `MemoryRepository`.
5. Cognito sends the new user its standard invitation. Existing Cognito identities are added to the company without resetting credentials.
6. The invitee completes Cognito's first-login flow and then signs into the installer or MCP client.

## Security and failure handling

- The request cannot supply company ID, user ID, or Owner role. Company scope comes from the authenticated context; invite roles are allowlisted and Owner is excluded.
- Cognito administration uses the EC2 instance role scoped to the configured User Pool ARN.
- A new Cognito user is created with invitation delivery suppressed until its OPM user and membership are persisted. If persistence fails, the new Cognito user is deleted; if cleanup fails, the response requires an administrator to remove the unused Cognito account. If resending the invitation fails after persistence, the response explains that membership exists and an administrator must resend the invitation.
- Cognito self-sign-up and pilot auto-enrollment are disabled. Missing users or memberships fail closed.
- Key generation remains a separate authenticated step. The observed “Sign in first” alert is caused by a missing access token in the browser tab's session storage, so the installer should require a current authenticated session before key creation.

## Verification

Verify unauthenticated and non-Owner requests fail, invalid emails and Owner role assignment are rejected, invitation persists identity/membership and returns no secret, duplicate membership is handled without duplicate Cognito accounts, and a successfully invited user can sign in. Run the non-AI suite and Ruff before release; exercise the invitation email and Cognito first-login flow in a pilot environment.
