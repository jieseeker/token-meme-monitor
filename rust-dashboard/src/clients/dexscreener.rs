//! DexScreener API client.

use reqwest::Client;
use serde_json::Value;
use std::time::Duration;

pub struct DexScreenerClient {
    client: Client,
}

impl DexScreenerClient {
    pub fn new() -> Self {
        let client = Client::builder()
            .timeout(Duration::from_secs(15))
            .user_agent("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36")
            .no_proxy()
            .build()
            .expect("failed to build HTTP client");
        Self { client }
    }

    /// Fetch all BSC pools for one token, sorted by liquidity.
    pub async fn pairs_for_token(
        &self,
        token_address: &str,
    ) -> Result<Vec<Value>, Box<dyn std::error::Error + Send + Sync>> {
        let url = format!("https://api.dexscreener.com/token-pairs/v1/bsc/{token_address}");
        let resp = self.client.get(&url).send().await?;

        if !resp.status().is_success() {
            return Err(format!("DexScreener token pairs returned {}", resp.status()).into());
        }

        let body: Value = resp.json().await?;
        let pairs = body.as_array().cloned().unwrap_or_default();

        // DexScreener's priceUsd is for baseToken, so keep exact BSC base-token pools.
        let mut bsc_pairs: Vec<Value> = pairs
            .into_iter()
            .filter(|p| {
                p.get("chainId").and_then(|c| c.as_str()) == Some("bsc")
                    && p.get("baseToken")
                        .and_then(|token| token.get("address"))
                        .and_then(|address| address.as_str())
                        .is_some_and(|address| address.eq_ignore_ascii_case(token_address))
            })
            .collect();

        bsc_pairs.sort_by(|a, b| {
            let la = a
                .get("liquidity")
                .and_then(|l| l.get("usd"))
                .and_then(|v| v.as_f64())
                .unwrap_or(0.0);
            let lb = b
                .get("liquidity")
                .and_then(|l| l.get("usd"))
                .and_then(|v| v.as_f64())
                .unwrap_or(0.0);
            lb.partial_cmp(&la).unwrap_or(std::cmp::Ordering::Equal)
        });

        Ok(bsc_pairs)
    }
}
