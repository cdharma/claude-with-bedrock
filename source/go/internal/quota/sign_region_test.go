package quota

// Regression tests for the IDC quota-check signing region.
//
// Bug: the SigV4 quota check was signed with the caller-supplied region
// (the IDC/SSO region on the IDC path). When Identity Center lives in a
// different region than the deployment (e.g. IDC in us-east-1, quota API in
// ap-south-1), the credential scope was wrong and API Gateway rejected the
// request with 403 — silently breaking quota (or hard-failing in fail-closed
// mode). Fix: derive the signing region from the quota endpoint URL itself
// (…execute-api.<region>.amazonaws.com), falling back to the caller-supplied
// region only for non-standard endpoints.

import (
	"context"
	"encoding/json"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/aws/aws-sdk-go-v2/aws"
)

func TestRegionFromEndpoint(t *testing.T) {
	cases := []struct {
		name     string
		endpoint string
		want     string
	}{
		{"standard partition", "https://abc123.execute-api.ap-south-1.amazonaws.com/prod", "ap-south-1"},
		{"standard partition no stage", "https://abc123.execute-api.us-east-1.amazonaws.com", "us-east-1"},
		{"china partition", "https://abc123.execute-api.cn-north-1.amazonaws.com.cn/prod", "cn-north-1"},
		{"govcloud", "https://abc123.execute-api.us-gov-west-1.amazonaws.com/prod", "us-gov-west-1"},
		{"uppercase host", "https://ABC123.EXECUTE-API.EU-WEST-1.AMAZONAWS.COM/prod", "eu-west-1"},
		{"custom domain", "https://quota.example.com/prod", ""},
		{"amazonaws host without execute-api", "https://s3.us-east-1.amazonaws.com", ""},
		{"missing api id", "https://execute-api.us-east-1.amazonaws.com", ""},
		{"scheme-less input", "abc123.execute-api.us-east-1.amazonaws.com/prod", ""},
		{"garbage", "not a url at all", ""},
		{"empty", "", ""},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := regionFromEndpoint(tc.endpoint); got != tc.want {
				t.Errorf("regionFromEndpoint(%q) = %q, want %q", tc.endpoint, got, tc.want)
			}
		})
	}
}

// captureAuthServer returns a test server whose handler records the incoming
// Authorization header and replies with an allowed quota result.
func captureAuthServer(t *testing.T, gotAuth *string) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		*gotAuth = r.Header.Get("Authorization")
		_ = json.NewEncoder(w).Encode(Result{Allowed: true, Reason: "within_limits"})
	}))
	t.Cleanup(srv.Close)
	return srv
}

// TestCheckWithResolvedCreds_SignsWithEndpointRegion asserts the SigV4
// credential scope uses the region embedded in the quota endpoint URL, NOT
// the caller-supplied (IDC/SSO) region. The synthetic execute-api hostname
// is routed to a local test server via the package's transport seam.
func TestCheckWithResolvedCreds_SignsWithEndpointRegion(t *testing.T) {
	var gotAuth string
	srv := captureAuthServer(t, &gotAuth)

	srvAddr := srv.Listener.Addr().String()
	orig := sigv4Transport
	sigv4Transport = &http.Transport{
		DialContext: func(ctx context.Context, network, addr string) (net.Conn, error) {
			return net.Dial("tcp", srvAddr)
		},
	}
	t.Cleanup(func() { sigv4Transport = orig })

	creds := aws.Credentials{AccessKeyID: "AKIDEXAMPLE", SecretAccessKey: "test-secret"}
	endpoint := "http://abc123.execute-api.ap-south-1.amazonaws.com/prod"

	// us-east-1 stands in for a mismatched IDC/SSO region.
	result := CheckWithResolvedCreds(endpoint, creds, "us-east-1", 5, "closed")
	if !result.Allowed {
		t.Fatalf("expected allowed=true, got reason=%q message=%q", result.Reason, result.Message)
	}
	if gotAuth == "" {
		t.Fatal("request never reached the test server (no Authorization header captured)")
	}
	if !strings.Contains(gotAuth, "/ap-south-1/execute-api/aws4_request") {
		t.Errorf("credential scope must use the quota API's own region ap-south-1, got %q", gotAuth)
	}
	if strings.Contains(gotAuth, "/us-east-1/") {
		t.Errorf("credential scope must not use the caller-supplied IDC region, got %q", gotAuth)
	}
}

// TestCheckWithResolvedCreds_FallsBackToCallerRegion asserts that when the
// endpoint hostname is not a standard execute-api URL (custom domain — here
// the test server's own 127.0.0.1 address), the caller-supplied region is
// used for signing, preserving pre-fix behavior.
func TestCheckWithResolvedCreds_FallsBackToCallerRegion(t *testing.T) {
	var gotAuth string
	srv := captureAuthServer(t, &gotAuth)

	creds := aws.Credentials{AccessKeyID: "AKIDEXAMPLE", SecretAccessKey: "test-secret"}
	result := CheckWithResolvedCreds(srv.URL, creds, "eu-central-1", 5, "closed")
	if !result.Allowed {
		t.Fatalf("expected allowed=true, got reason=%q message=%q", result.Reason, result.Message)
	}
	if !strings.Contains(gotAuth, "/eu-central-1/execute-api/aws4_request") {
		t.Errorf("credential scope should fall back to the caller-supplied region eu-central-1, got %q", gotAuth)
	}
}
