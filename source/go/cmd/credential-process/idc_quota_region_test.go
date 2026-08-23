package main

// Regression test for the IDC quota-check signing region fallback.
//
// The IDC path used to pass its resolved region (idc_region, falling back to
// aws_region) straight into the quota SigV4 signer. When Identity Center is
// in a different region than the deployment (e.g. IDC in us-east-1, quota
// API in ap-south-1), that produced a credential scope API Gateway rejects
// with 403. The signing region now comes from the quota endpoint URL itself
// (internal/quota), and the FALLBACK passed from idc.go must prefer the
// deployment region (aws_region) over the IDC region.

import "testing"

func TestQuotaSigningFallbackRegion(t *testing.T) {
	cases := []struct {
		name      string
		awsRegion string
		idcRegion string
		want      string
	}{
		{
			// The regression scenario: IDC region differs from the deployment
			// region. The quota API lives in the deployment region, so the
			// fallback must be aws_region, never the IDC region.
			name:      "prefers deployment region over IDC region",
			awsRegion: "ap-south-1",
			idcRegion: "us-east-1",
			want:      "ap-south-1",
		},
		{
			name:      "falls back to IDC region when aws_region empty",
			awsRegion: "",
			idcRegion: "us-east-1",
			want:      "us-east-1",
		},
		{
			name:      "same region either way",
			awsRegion: "us-west-2",
			idcRegion: "us-west-2",
			want:      "us-west-2",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := quotaSigningFallbackRegion(tc.awsRegion, tc.idcRegion); got != tc.want {
				t.Errorf("quotaSigningFallbackRegion(%q, %q) = %q, want %q",
					tc.awsRegion, tc.idcRegion, got, tc.want)
			}
		})
	}
}
