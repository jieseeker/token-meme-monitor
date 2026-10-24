//! Binance Alpha token list API client.

use reqwest::Client;
use serde_json::Value;
use std::time::Duration;

pub struct BinanceAlphaClient {
    client: Client,
    url: String,
}

#[derive(Clone, Debug)]
pub struct AlphaToken {
    pub address: String,
    pub symbol: String,
    pub name: String,
    pub raw: Value,
}

impl BinanceAlphaClient {
    pub fn new(url: &str) -> Self {
        let client = Client::builder()
            .timeout(Duration::from_secs(15))
            .user_agent("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36")
            .no_proxy()
            .build()
            .expect("failed to build HTTP client");
        Self {
            client,
            url: url.to_string(),
        }
    }

    /// Fetch currently active Binance Alpha BSC tokens.
    pub async fn fetch_bsc_tokens(
        &self,
    ) -> Result<Vec<AlphaToken>, Box<dyn std::error::Error + Send + Sync>> {
        let resp = self
            .client
            .get(&self.url)
            .send()
            .await?
            .error_for_status()?;
        let body: Value = resp.json().await?;

        Ok(parse_bsc_tokens(&body)?)
    }
}

fn parse_bsc_tokens(body: &Value) -> Result<Vec<AlphaToken>, &'static str> {
    let data = body
        .get("data")
        .and_then(|d| d.as_array())
        .ok_or("Binance Alpha token list has no data array")?;
    let tokens: Vec<AlphaToken> = data
        .iter()
        .filter(|item| item.get("chainId").and_then(|c| c.as_str()) == Some("56"))
        .filter(|item| item["offline"] != true && item["fullyDelisted"] != true)
        .filter(|item| item.get("binanceFuturesListed").and_then(|v| v.as_bool()) == Some(true))
        .filter_map(|item| {
            let address = item
                .get("contractAddress")
                .and_then(|a| a.as_str())?
                .to_lowercase();
            if address.len() != 42
                || !address.starts_with("0x")
                || !address[2..].bytes().all(|b| b.is_ascii_hexdigit())
            {
                return None;
            }
            let symbol = item
                .get("symbol")
                .and_then(|s| s.as_str())
                .unwrap_or_default()
                .to_string();
            let name = item
                .get("name")
                .and_then(|n| n.as_str())
                .unwrap_or_default()
                .to_string();
            Some(AlphaToken {
                address,
                symbol,
                name,
                raw: item.clone(),
            })
        })
        .collect();

    Ok(tokens)
}

#[cfg(test)]
mod tests {
    #[test]
    fn bsc_list_excludes_offline_alpha_tokens() {
        let body = serde_json::json!({"data": [
            {"chainId":"56","contractAddress":"0x1111111111111111111111111111111111111111","symbol":"LIVE","offline":false,"binanceFuturesListed":true},
            {"chainId":"56","contractAddress":"0x2222222222222222222222222222222222222222","symbol":"OWL","offline":true,"binanceFuturesListed":true},
            {"chainId":"56","contractAddress":"0x3333333333333333333333333333333333333333","symbol":"OLD","fullyDelisted":true,"binanceFuturesListed":true},
            {"chainId":"56","contractAddress":"0x5555555555555555555555555555555555555555","symbol":"NOFUT","offline":false,"binanceFuturesListed":false},
            {"chainId":"1","contractAddress":"0x4444444444444444444444444444444444444444","symbol":"ETH"}
        ]});
        let symbols: Vec<_> = super::parse_bsc_tokens(&body)
            .unwrap()
            .into_iter()
            .map(|token| token.symbol)
            .collect();
        assert_eq!(symbols, ["LIVE"]);
    }
}
