package quota

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	v4 "github.com/aws/aws-sdk-go-v2/aws/signer/v4"
	awsconfig "github.com/aws/aws-sdk-go-v2/config"
)

// sigv4Transport is the HTTP transport used for SigV4-signed quota requests.
// nil means http.DefaultTransport. Tests override it to route synthetic
// execute-api hostnames to a local test server so the signed Authorization
// header (credential scope) can be inspected.
var sigv4Transport http.RoundTripper

// Result represents the quota check API response.
type Result struct {
	Allowed bool                   `json:"allowed"`
	Reason  string                 `json:"reason"`
	Message string                 `json:"message"`
	Usage   map[string]interface{} `json:"usage"`
	Policy  map[string]interface{} `json:"policy"`
}

// Check calls the quota API endpoint with the given JWT token.
// When idToken is empty, falls back to IAM/SigV4 auth (IDC path).
func Check(endpoint, idToken string, timeout int, failMode string) *Result {
	if idToken == "" {
		// No JWT token — try IAM auth (IDC path)
		return CheckWithIAM(endpoint, timeout, failMode)
	}
	client := &http.Client{Timeout: time.Duration(timeout) * time.Second}

	req, err := http.NewRequest("GET", endpoint+"/check", nil)
	if err != nil {
		return failResult(failMode, "error", fmt.Sprintf("creating request: %v", err))
	}
	req.Header.Set("Authorization", "Bearer "+idToken)

	resp, err := client.Do(req)
	if err != nil {
		return failResult(failMode, "connection_error", fmt.Sprintf("Could not connect to quota service: %v", err))
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)

	switch resp.StatusCode {
	case 200:
		var result Result
		if err := json.Unmarshal(body, &result); err != nil {
			return failResult(failMode, "parse_error", "Could not parse quota response")
		}
		return &result
	case 401:
		return failResult(failMode, "jwt_invalid", "Quota check authentication failed - invalid or expired token")
	default:
		return failResult(failMode, "api_error", fmt.Sprintf("Quota check failed with status %d", resp.StatusCode))
	}
}

func failResult(failMode, reason, message string) *Result {
	if failMode == "closed" {
		return &Result{Allowed: false, Reason: reason, Message: message}
	}
	return &Result{Allowed: true, Reason: reason}
}

// CheckWithIAM calls the quota API using AWS SigV4 authentication.
// Used by IAM Identity Center (IDC) users who don't have a JWT token.
// The API Gateway validates the IAM credentials and the Lambda extracts
// the user email from the IAM caller ARN session name.
func CheckWithIAM(endpoint string, timeout int, failMode string) *Result {
	ctx := context.Background()

	cfg, err := awsconfig.LoadDefaultConfig(ctx)
	if err != nil {
		return failResult(failMode, "iam_config_error", fmt.Sprintf("Could not load AWS config: %v", err))
	}

	creds, err := cfg.Credentials.Retrieve(ctx)
	if err != nil {
		return failResult(failMode, "iam_creds_error", fmt.Sprintf("Could not retrieve AWS credentials: %v", err))
	}

	return checkWithSigV4(endpoint, creds, cfg.Region, timeout, failMode)
}

// CheckWithResolvedCreds calls the quota API using pre-resolved AWS credentials.
// Use this when credentials have already been obtained (e.g. from IDC SSO flow)
// and the default credential chain may resolve a different principal.
func CheckWithResolvedCreds(endpoint string, creds aws.Credentials, region string, timeout int, failMode string) *Result {
	return checkWithSigV4(endpoint, creds, region, timeout, failMode)
}

// regionFromEndpoint extracts the AWS region embedded in an API Gateway
// execute-api endpoint URL:
//
//	https://abc123.execute-api.ap-south-1.amazonaws.com/prod   -> "ap-south-1"
//	https://abc123.execute-api.cn-north-1.amazonaws.com.cn/p   -> "cn-north-1"
//
// Returns "" when the endpoint doesn't look like a standard execute-api URL
// (custom domains, garbage input), so callers can fall back to a configured
// region.
func regionFromEndpoint(endpoint string) string {
	u, err := url.Parse(endpoint)
	if err != nil {
		return ""
	}
	host := strings.ToLower(u.Hostname())

	var suffix string
	switch {
	case strings.HasSuffix(host, ".amazonaws.com"):
		suffix = ".amazonaws.com"
	case strings.HasSuffix(host, ".amazonaws.com.cn"):
		suffix = ".amazonaws.com.cn"
	default:
		return ""
	}

	labels := strings.Split(strings.TrimSuffix(host, suffix), ".")
	// Expect at least <api-id>.execute-api.<region>
	if len(labels) < 3 {
		return ""
	}
	region := labels[len(labels)-1]
	if labels[len(labels)-2] != "execute-api" || region == "" {
		return ""
	}
	return region
}

// checkWithSigV4 signs and sends the quota check request using the provided credentials.
func checkWithSigV4(endpoint string, creds aws.Credentials, region string, timeout int, failMode string) *Result {
	ctx := context.Background()

	url := endpoint + "/check"
	req, err := http.NewRequestWithContext(ctx, "GET", url, nil)
	if err != nil {
		return failResult(failMode, "error", fmt.Sprintf("creating request: %v", err))
	}

	// Sign for the region the quota API actually lives in — the endpoint URL
	// embeds it (…execute-api.<region>.amazonaws.com). The caller-supplied
	// region is only a fallback for non-standard endpoints. Signing with any
	// other region (e.g. the IDC/SSO region when Identity Center lives in a
	// different region than the deployment) produces a credential scope that
	// API Gateway rejects with 403.
	signRegion := regionFromEndpoint(endpoint)
	if signRegion == "" {
		signRegion = region
	}

	// Sign the request with SigV4 for execute-api service
	signer := v4.NewSigner()
	hash := "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855" // SHA256 of empty body
	err = signer.SignHTTP(ctx, creds, req, hash, "execute-api", signRegion, time.Now())
	if err != nil {
		return failResult(failMode, "sigv4_error", fmt.Sprintf("Could not sign request: %v", err))
	}

	client := &http.Client{Timeout: time.Duration(timeout) * time.Second, Transport: sigv4Transport}
	resp, err := client.Do(req)
	if err != nil {
		return failResult(failMode, "connection_error", fmt.Sprintf("Could not connect to quota service: %v", err))
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)

	switch resp.StatusCode {
	case 200:
		var result Result
		if err := json.Unmarshal(body, &result); err != nil {
			return failResult(failMode, "parse_error", "Could not parse quota response")
		}
		return &result
	case 403:
		return failResult(failMode, "iam_forbidden", "Quota check IAM authentication failed - check execute-api:Invoke permission")
	default:
		return failResult(failMode, "api_error", fmt.Sprintf("Quota check failed with status %d", resp.StatusCode))
	}
}
