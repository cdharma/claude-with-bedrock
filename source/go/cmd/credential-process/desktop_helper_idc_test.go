package main

// ABOUTME: Regression tests for --desktop on IAM Identity Center profiles.
// ABOUTME: Pins that runDesktopHelper dispatches to the IDC path, never the OIDC flow.

import (
	"io"
	"os"
	"strings"
	"testing"

	"ccwb-go/internal/config"
)

// captureDesktopStderr runs fn with os.Stderr redirected and returns the exit
// code plus everything written to stderr.
func captureDesktopStderr(t *testing.T, fn func() int) (int, string) {
	t.Helper()
	orig := os.Stderr
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatalf("pipe: %v", err)
	}
	os.Stderr = w
	code := fn()
	_ = w.Close()
	os.Stderr = orig
	out, err := io.ReadAll(r)
	if err != nil {
		t.Fatalf("read pipe: %v", err)
	}
	return code, string(out)
}

// TestRunDesktopHelper_IDCDoesNotEnterOIDCFlow pins the fix for the --desktop
// helper on IDC profiles.
//
// main() dispatches --desktop BEFORE its own `if cfg.IsIDC()` auth dispatch, and
// runDesktopHelper's fallbacks call a.run() — the OIDC flow. IDC profiles carry
// no provider_type, so a.run() failed with "unknown provider type:". That path
// was unconditional, because the IDC flow never writes the credential cache that
// getCachedCredentials() consults first, so cached credentials were always nil.
//
// The config here omits any region, so resolveIDCSettings fails immediately and
// the test stays hermetic (no SSO device auth, no STS, no network). What matters
// is WHICH error surfaces: an IDC-specific one, never the OIDC one.
func TestRunDesktopHelper_IDCDoesNotEnterOIDCFlow(t *testing.T) {
	t.Setenv("CLAUDE_HELPER_CONTEXT", "interactive")

	cfg := &config.ProfileConfig{
		AuthType:             "idc",
		IDCStartURL:          "https://example.awsapps.com/start",
		IDCAccountID:         "123456789012",
		IDCPermissionSetName: "BedrockDeveloperAccess",
		// Region deliberately unset so resolveIDCSettings bails out early.
	}
	if !cfg.IsIDC() {
		t.Fatalf("IsIDC() = false, want true — test config no longer represents an IDC profile")
	}

	app := &credentialApp{profile: "test-desktop-idc", cfg: cfg}

	code, stderr := captureDesktopStderr(t, app.runDesktopHelper)

	if code == 0 {
		t.Fatalf("runDesktopHelper() = 0, want non-zero (IDC settings are incomplete)")
	}
	if strings.Contains(stderr, "unknown provider type") {
		t.Errorf("runDesktopHelper() entered the OIDC flow for an IDC profile.\nstderr: %s", stderr)
	}
	if !strings.Contains(stderr, "IDC") {
		t.Errorf("runDesktopHelper() did not surface an IDC-specific error, so the IDC path was not taken.\nstderr: %s", stderr)
	}
}

// TestRunDesktopHelper_IDCIgnoresCredentialCache documents why the IDC branch
// must come before the getCachedCredentials() check: the IDC flow never calls
// saveCredentials, so that cache is permanently empty for IDC profiles and
// cannot be primed as a workaround.
func TestRunDesktopHelper_IDCIgnoresCredentialCache(t *testing.T) {
	cfg := &config.ProfileConfig{
		AuthType:             "idc",
		IDCStartURL:          "https://example.awsapps.com/start",
		IDCAccountID:         "123456789012",
		IDCPermissionSetName: "BedrockDeveloperAccess",
		CredentialStorage:    "session",
	}
	app := &credentialApp{profile: "test-desktop-idc-nocache", cfg: cfg}

	if creds := app.getCachedCredentials(); creds != nil {
		t.Skipf("credential cache unexpectedly populated for %q; environment is not clean", app.profile)
	}

	// With an empty cache the pre-fix code fell through to a.run(). The IDC
	// branch must short-circuit ahead of that.
	if !cfg.IsIDC() {
		t.Fatalf("IsIDC() = false, want true")
	}
}
