//! BSC pool depth: USD needed to move the selected pool's spot price.

use reqwest::{Client, StatusCode};
use serde_json::{json, Value};
use std::{
    future::Future,
    sync::{
        atomic::{AtomicU64, AtomicUsize, Ordering},
        OnceLock,
    },
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

const PUBLIC_RPC_ENDPOINTS: [&str; 4] = [
    "https://bsc-dataseed.binance.org/",
    "https://bsc-dataseed.bnbchain.org",
    "https://bsc-dataseed.nariox.org",
    "https://bsc-dataseed.defibit.io",
];
static NEXT_RPC: AtomicUsize = AtomicUsize::new(0);
static RPC_ENDPOINTS: OnceLock<Vec<RpcEndpoint>> = OnceLock::new();
const PANCAKE_FACTORY: &str = "0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865";
const UNISWAP_FACTORY: &str = "0xdB1d10011AD0Ff90774D0C6Bb92e5C5c8b4461F7";
const PANCAKE_QUOTER: &str = "0xB048Bbc1Ee6b733FFfCFb9e9CeF7375518e25997";
const UNISWAP_QUOTER: &str = "0x78D78E420Da98ad378D7799bE8f4AF69033EB077";
const BANDS: [f64; 6] = [0.01, 0.02, 0.05, 0.10, 0.15, 0.20];

struct RpcContext<'a> {
    client: &'a Client,
    endpoint: &'a str,
    requests: AtomicUsize,
    retry_after: AtomicU64,
}

struct RpcEndpoint {
    url: String,
    cooldown_until: AtomicU64,
}

fn rpc_endpoints() -> &'static [RpcEndpoint] {
    RPC_ENDPOINTS.get_or_init(|| {
        let mut urls: Vec<String> = std::env::var("BSC_DEPTH_RPC_URLS")
            .unwrap_or_default()
            .split(',')
            .map(str::trim)
            .filter(|url| url.starts_with("https://") && url.parse::<reqwest::Url>().is_ok())
            .map(str::to_string)
            .collect();
        for url in PUBLIC_RPC_ENDPOINTS {
            if !urls.iter().any(|configured| configured == url) {
                urls.push(url.to_string());
            }
        }
        urls.into_iter()
            .map(|url| RpcEndpoint {
                url,
                cooldown_until: AtomicU64::new(0),
            })
            .collect()
    })
}

fn rpc_order(start: usize, now: u64, cooldowns: &[u64]) -> Vec<usize> {
    (0..cooldowns.len())
        .map(|offset| (start + offset) % cooldowns.len())
        .filter(|index| cooldowns[*index] <= now)
        .collect()
}

fn rpc_retry_delay(status: StatusCode, body: Option<&Value>) -> u64 {
    let code = body.and_then(|body| body["error"]["code"].as_i64());
    let message = body
        .and_then(|body| body["error"]["message"].as_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    if status == StatusCode::TOO_MANY_REQUESTS
        || code == Some(-32005)
        || message.contains("rate limit")
        || message.contains("too many requests")
    {
        60
    } else if status.is_server_error() {
        15
    } else {
        0
    }
}

fn unix_seconds() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

fn word(data: &str, index: usize) -> Option<&str> {
    data.get(2 + index * 64..2 + (index + 1) * 64)
}
fn number(data: &str, index: usize) -> Option<f64> {
    word(data, index)?.bytes().try_fold(0.0, |n, c| {
        (c as char)
            .to_digit(16)
            .map(|digit| n * 16.0 + digit as f64)
    })
}
fn address(data: &str) -> Option<String> {
    Some(format!("0x{}", word(data, 0)?.get(24..64)?).to_ascii_lowercase())
}
fn field<'a>(row: &'a Value, key: &str) -> Result<&'a str, String> {
    row[key].as_str().ok_or_else(|| format!("缺少 {key}"))
}
fn abi_address(addr: &str) -> String {
    format!("{:0>64}", &addr[2..])
}

async fn rpc(ctx: &RpcContext<'_>, method: &str, params: Value) -> Result<String, String> {
    if ctx.retry_after.load(Ordering::Relaxed) > 0 {
        return Err("BSC RPC 节点暂时不可用".into());
    }
    ctx.requests.fetch_add(1, Ordering::Relaxed);
    let response = ctx
        .client
        .post(ctx.endpoint)
        .json(&json!({"jsonrpc":"2.0","id":1,"method":method,"params":params}))
        .send()
        .await
        .map_err(|_| {
            ctx.retry_after.fetch_max(15, Ordering::Relaxed);
            "BSC RPC 连接失败".to_string()
        })?;
    ctx.retry_after
        .fetch_max(rpc_retry_delay(response.status(), None), Ordering::Relaxed);
    if !response.status().is_success() {
        return Err(format!("BSC RPC HTTP {}", response.status()));
    }
    let body: Value = response.json().await.map_err(|_| {
        ctx.retry_after.fetch_max(15, Ordering::Relaxed);
        "BSC RPC 返回无效 JSON".to_string()
    })?;
    ctx.retry_after.fetch_max(
        rpc_retry_delay(StatusCode::OK, Some(&body)),
        Ordering::Relaxed,
    );
    body["result"]
        .as_str()
        .map(str::to_string)
        .ok_or_else(|| format!("BSC RPC 错误 {}", body["error"]["code"]))
}
async fn call(ctx: &RpcContext<'_>, pool: &str, data: &str, block: &str) -> Result<String, String> {
    rpc(ctx, "eth_call", json!([{"to":pool,"data":data},block])).await
}
async fn pool_field(
    ctx: &RpcContext<'_>,
    pool: &str,
    selector: &str,
    block: &str,
) -> Result<String, String> {
    call(ctx, pool, selector, block).await
}

async fn quote_sqrt(
    ctx: &RpcContext<'_>,
    quoter: &str,
    token_in: &str,
    token_out: &str,
    fee: u32,
    raw: u128,
    block: &str,
) -> Option<f64> {
    let data = format!(
        "0xc6a5026a{}{}{:064x}{:064x}{:064x}",
        abi_address(token_in),
        abi_address(token_out),
        raw,
        fee,
        0
    );
    let result = call(ctx, quoter, &data, block).await.ok()?;
    number(&result, 1)
}

async fn find_band<F, Fut, R>(
    mut low: f64,
    cap: f64,
    guess: f64,
    top: f64,
    reached: R,
    mut probe: F,
) -> Option<(f64, f64)>
where
    F: FnMut(f64) -> Fut,
    Fut: Future<Output = Option<f64>>,
    R: Fn(f64) -> bool,
{
    let mut high = guess.max(low + 1.0).min(cap);
    let mut high_value = if high == cap { top } else { probe(high).await? };
    while !reached(high_value) && high < cap {
        low = high;
        high = (high * 2.0).min(cap);
        high_value = if high == cap { top } else { probe(high).await? };
    }
    if !reached(high_value) {
        return None;
    }
    for _ in 0..20 {
        // ponytail: 3% quote-amount precision is enough for approximate depth; use tick math if tighter estimates are needed without more RPC calls.
        if high - low <= (high * 0.03).max(1.0) {
            break;
        }
        let mid = (low + high) / 2.0;
        let value = probe(mid).await?;
        if reached(value) {
            high = mid;
            high_value = value;
        } else {
            low = mid;
        }
    }
    Some((high, high_value))
}

async fn v3_depth(
    ctx: &RpcContext<'_>,
    quoter: &str,
    token_in: &str,
    token_out: &str,
    fee: u32,
    initial_sqrt: f64,
    input_usd: f64,
    input_decimals: u32,
    base_is_token0: bool,
    upward: bool,
    liquidity_usd: f64,
    block: &str,
) -> Vec<Option<f64>> {
    let cap = (liquidity_usd * 2.0).clamp(100.0, 20_000_000.0);
    let price_ratio = |sqrt: f64| {
        let ratio = (sqrt / initial_sqrt).powi(2);
        if base_is_token0 {
            ratio
        } else {
            1.0 / ratio
        }
    };
    let reached = |sqrt: f64, pct: f64| {
        let ratio = price_ratio(sqrt);
        if upward {
            ratio >= 1.0 + pct
        } else {
            ratio <= 1.0 - pct
        }
    };
    let quote = |usd: f64| {
        let raw = usd / input_usd * 10_f64.powi(input_decimals as i32);
        (raw.is_finite() && raw >= 1.0 && raw < u128::MAX as f64).then_some(raw as u128)
    };
    let Some(raw) = quote(cap) else {
        return vec![None; BANDS.len()];
    };
    let Some(top) = quote_sqrt(ctx, quoter, token_in, token_out, fee, raw, block).await else {
        return vec![None; BANDS.len()];
    };
    let mut result = Vec::with_capacity(BANDS.len());
    let mut previous = None;
    for (index, pct) in BANDS.into_iter().enumerate() {
        if !reached(top, pct) {
            result.push(None);
            continue;
        }
        if let Some((amount, sqrt)) = previous {
            if reached(sqrt, pct) {
                result.push(Some(amount));
                continue;
            }
        }
        let low = previous.map_or(0.0, |(amount, _)| amount);
        let top_change = if upward {
            price_ratio(top) - 1.0
        } else {
            1.0 - price_ratio(top)
        };
        let guess = if index > 0 {
            previous.map_or(cap * pct / top_change, |(amount, _)| {
                amount * pct / BANDS[index - 1]
            })
        } else {
            cap * pct / top_change
        }
        .clamp(
            (cap / 4096.0)
                .max(input_usd / 10_f64.powi(input_decimals as i32) * 1.000001)
                .min(cap),
            cap,
        );
        let found = find_band(
            low,
            cap,
            guess,
            top,
            |sqrt| reached(sqrt, pct),
            |usd| async move {
                let raw = quote(usd)?;
                quote_sqrt(ctx, quoter, token_in, token_out, fee, raw, block).await
            },
        )
        .await;
        if let Some(value) = found {
            previous = Some(value);
        }
        result.push(found.map(|(amount, _)| amount));
    }
    result
}

pub async fn calculate(pair: &Value) -> Value {
    let started = Instant::now();
    let client = match Client::builder()
        .timeout(Duration::from_secs(8))
        .no_proxy()
        .build()
    {
        Ok(client) => client,
        Err(error) => return json!({"available":false,"reason":error.to_string()}),
    };
    let endpoints = rpc_endpoints();
    let cooldowns: Vec<u64> = endpoints
        .iter()
        .map(|endpoint| endpoint.cooldown_until.load(Ordering::Relaxed))
        .collect();
    let start = NEXT_RPC.fetch_add(1, Ordering::Relaxed) % endpoints.len();
    let mut requests = 0;
    let mut attempts = 0;
    let mut endpoint_index = None;
    let mut value = json!({"available":false,"reason":"BSC RPC 暂时不可用"});
    for index in rpc_order(start, unix_seconds(), &cooldowns) {
        if endpoints[index].cooldown_until.load(Ordering::Relaxed) > unix_seconds() {
            continue;
        }
        if attempts == 3 {
            break;
        }
        attempts += 1;
        endpoint_index = Some(index);
        let ctx = RpcContext {
            client: &client,
            endpoint: &endpoints[index].url,
            requests: AtomicUsize::new(0),
            retry_after: AtomicU64::new(0),
        };
        value = match calculate_inner(pair, &ctx).await {
            Ok(value) => value,
            Err(reason) => json!({"available":false,"reason":reason}),
        };
        requests += ctx.requests.load(Ordering::Relaxed);
        let retry_after = ctx.retry_after.load(Ordering::Relaxed);
        if retry_after == 0 {
            break;
        }
        endpoints[index]
            .cooldown_until
            .store(unix_seconds() + retry_after, Ordering::Relaxed);
        crate::logging::event(
            "warn",
            "depth_rpc_failover",
            json!({
                "pair":pair["pair_address"], "endpoint_index":index,
                "retry_after_seconds":retry_after, "attempt":attempts
            }),
        );
    }
    crate::logging::event(
        "info",
        "depth_calculation_completed",
        json!({
            "pair":pair["pair_address"], "available":value["available"], "type":value["type"],
            "rpc_requests":requests, "rpc_attempts":attempts, "endpoint_index":endpoint_index,
            "elapsed_ms":started.elapsed().as_millis()
        }),
    );
    value
}

async fn calculate_inner(pair: &Value, ctx: &RpcContext<'_>) -> Result<Value, String> {
    let pool = field(pair, "pair_address")?;
    let base = field(pair, "token_address")?;
    let dex = field(pair, "dex_id")?;
    if pair["chain_id"] != "bsc"
        || !matches!(dex, "pancakeswap" | "uniswap")
        || ![pool, base].iter().all(|a| {
            a.len() == 42
                && a.starts_with("0x")
                && a[2..].bytes().all(|c| (c as char).is_ascii_hexdigit())
        })
    {
        return Err("仅支持 BSC PancakeSwap / Uniswap 的有效池子".into());
    }
    let base_usd = pair["price_usd"]
        .as_f64()
        .filter(|v| v.is_finite() && *v > 0.0)
        .ok_or("缺少代币美元价格")?;
    let liquidity_usd = pair["liquidity_usd"]
        .as_f64()
        .filter(|v| v.is_finite() && *v > 0.0)
        .ok_or("缺少池子流动性")?;
    let block = rpc(ctx, "eth_blockNumber", json!([])).await?;
    let token0 =
        address(&pool_field(ctx, pool, "0x0dfe1681", &block).await?).ok_or("无法读取 token0")?;
    let token1 =
        address(&pool_field(ctx, pool, "0xd21220a7", &block).await?).ok_or("无法读取 token1")?;
    let base_is_token0 = base.eq_ignore_ascii_case(&token0);
    if !base_is_token0 && !base.eq_ignore_ascii_case(&token1) {
        return Err("池子代币与监控代币不匹配".into());
    }
    let quote = if base_is_token0 { &token1 } else { &token0 };
    let d0 = number(&pool_field(ctx, &token0, "0x313ce567", &block).await?, 0)
        .filter(|v| *v <= 36.0)
        .ok_or("token0 精度无效")? as u32;
    let d1 = number(&pool_field(ctx, &token1, "0x313ce567", &block).await?, 0)
        .filter(|v| *v <= 36.0)
        .ok_or("token1 精度无效")? as u32;
    let (base_decimals, quote_decimals) = if base_is_token0 { (d0, d1) } else { (d1, d0) };

    if let Ok(reserves) = pool_field(ctx, pool, "0x0902f1ac", &block).await {
        let r0 = number(&reserves, 0)
            .filter(|v| *v > 0.0)
            .ok_or("池子储备无效")?
            / 10_f64.powi(d0 as i32);
        let r1 = number(&reserves, 1)
            .filter(|v| *v > 0.0)
            .ok_or("池子储备无效")?
            / 10_f64.powi(d1 as i32);
        let (base_reserve, quote_reserve) = if base_is_token0 { (r0, r1) } else { (r1, r0) };
        let quote_usd = base_usd * base_reserve / quote_reserve;
        let fee = if dex == "pancakeswap" { 0.0025 } else { 0.003 };
        let support: Vec<f64> = BANDS
            .iter()
            .map(|pct| base_reserve * (1.0 / (1.0 - pct).sqrt() - 1.0) * base_usd / (1.0 - fee))
            .collect();
        let pressure: Vec<f64> = BANDS
            .iter()
            .map(|pct| quote_reserve * ((1.0 + pct).sqrt() - 1.0) * quote_usd / (1.0 - fee))
            .collect();
        return Ok(
            json!({"available":true,"type":"v2","dex":dex,"block":block,"bands_pct":[1,2,5,10,15,20],"support_usd":support,"pressure_usd":pressure}),
        );
    }

    let slot0 = pool_field(ctx, pool, "0x3850c7bd", &block)
        .await
        .map_err(|_| "仅支持 V2 / V3 标准池子")?;
    let initial_sqrt = number(&slot0, 0)
        .filter(|v| *v > 0.0)
        .ok_or("V3 当前价格无效")?;
    let fee = number(&pool_field(ctx, pool, "0xddca3f43", &block).await?, 0)
        .filter(|v| *v <= 1_000_000.0)
        .ok_or("V3 费率无效")? as u32;
    let factory =
        address(&pool_field(ctx, pool, "0xc45a0155", &block).await?).ok_or("V3 工厂地址无效")?;
    let quoter = match dex {
        "pancakeswap" if factory.eq_ignore_ascii_case(PANCAKE_FACTORY) => PANCAKE_QUOTER,
        "uniswap" if factory.eq_ignore_ascii_case(UNISWAP_FACTORY) => UNISWAP_QUOTER,
        _ => return Err("池子工厂与 DEX 不匹配".into()),
    };
    let raw_ratio = (initial_sqrt / 2_f64.powi(96)).powi(2);
    let base_quote = if base_is_token0 {
        raw_ratio * 10_f64.powi(d0 as i32 - d1 as i32)
    } else {
        1.0 / raw_ratio * 10_f64.powi(d1 as i32 - d0 as i32)
    };
    let quote_usd = base_usd / base_quote;
    if !quote_usd.is_finite() || quote_usd <= 0.0 {
        return Err("报价代币价格无效".into());
    }
    let (support, pressure) = tokio::join!(
        v3_depth(
            ctx,
            quoter,
            base,
            quote,
            fee,
            initial_sqrt,
            base_usd,
            base_decimals,
            base_is_token0,
            false,
            liquidity_usd,
            &block
        ),
        v3_depth(
            ctx,
            quoter,
            quote,
            base,
            fee,
            initial_sqrt,
            quote_usd,
            quote_decimals,
            base_is_token0,
            true,
            liquidity_usd,
            &block
        )
    );
    if ctx.retry_after.load(Ordering::Relaxed) > 0 {
        return Err("BSC RPC 节点暂时不可用".into());
    }
    if support.iter().chain(&pressure).all(Option::is_none) {
        return Err("池子报价不可用或深度超过可计算范围".into());
    }
    Ok(
        json!({"available":true,"type":"v3","dex":dex,"block":block,"bands_pct":[1,2,5,10,15,20],"support_usd":support,"pressure_usd":pressure}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rpc_order_rotates_and_skips_cooled_nodes() {
        assert_eq!(rpc_order(1, 100, &[0, 150, 0, 0]), vec![2, 3, 0]);
        assert!(rpc_order(0, 100, &[101, 101, 101, 101]).is_empty());
    }
    #[test]
    fn rpc_retry_delay_distinguishes_rate_limit_from_contract_revert() {
        assert_eq!(
            rpc_retry_delay(reqwest::StatusCode::TOO_MANY_REQUESTS, None),
            60
        );
        assert_eq!(rpc_retry_delay(reqwest::StatusCode::BAD_GATEWAY, None), 15);
        assert_eq!(
            rpc_retry_delay(
                reqwest::StatusCode::OK,
                Some(&json!({"error":{"code":-32005,"message":"rate limit"}}))
            ),
            60
        );
        assert_eq!(
            rpc_retry_delay(
                reqwest::StatusCode::OK,
                Some(&json!({"error":{"code":3,"message":"execution reverted"}}))
            ),
            0
        );
    }
    #[tokio::test]
    async fn bracket_search_keeps_three_percent_accuracy_with_fewer_quotes() {
        let calls = std::cell::Cell::new(0);
        let (amount, _) = find_band(
            0.0,
            8_000_000.0,
            4_000.0,
            8_000_000.0,
            |price| price >= 4_350.0,
            |usd| {
                calls.set(calls.get() + 1);
                std::future::ready(Some(usd))
            },
        )
        .await
        .unwrap();
        assert!((4_350.0..=4_485.0).contains(&amount), "amount={amount}");
        assert!(calls.get() <= 7, "quotes={}", calls.get());
    }
    #[test]
    fn reads_abi_words_and_keeps_v2_depth_asymmetric() {
        let data = format!("0x{:064x}{:064x}", 100_u128, 400_u128);
        assert_eq!(number(&data, 1), Some(400.0));
        let base = 100.0;
        let down = base * (1.0 / (1.0_f64 - 0.05).sqrt() - 1.0);
        let up = base * ((1.0_f64 + 0.05).sqrt() - 1.0);
        assert!(down > up);
    }
}
