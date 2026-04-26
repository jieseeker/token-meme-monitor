from __future__ import annotations

import logging
import time
from dataclasses import replace
from datetime import datetime, timedelta

import requests

from token_meme_monitor.alerts import TelegramAlertClient
from token_meme_monitor.clients.binance_alpha import BinanceAlphaClient
from token_meme_monitor.clients.binance_alpha_rank import BinanceAlphaRankClient
from token_meme_monitor.clients.binance_market import BinanceMarketClient, build_binance_listing_labels
from token_meme_monitor.clients.bsc import BscPairDiscoveryClient
from token_meme_monitor.clients.dexscreener import DexScreenerClient
from token_meme_monitor.clients.honeypot import HoneypotClient
from token_meme_monitor.config import AppConfig
from token_meme_monitor.constants import SCAN_CURSOR_KEY
from token_meme_monitor.database import MonitorRepository
from token_meme_monitor.features import build_feature_vector
from token_meme_monitor.indicator_candidates import compute_candidate_indicators
from token_meme_monitor.market_data import build_alpha_reference, sanitize_alpha_metadata, sanitize_pair_snapshot
from token_meme_monitor.models import AlphaToken
from token_meme_monitor.prediction_outcomes import compute_prediction_outcome_with_hourly_ohlcv
from token_meme_monitor.predictions import PredictionCalibration, build_prediction_calibration, build_prediction_result
from token_meme_monitor.signals import SignalEngine
from token_meme_monitor.utils import parse_datetime, utcnow

LOGGER = logging.getLogger(__name__)
BINANCE_FUTURES_CACHE_KEY = "binance_futures_registry"


class MonitorWorker:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._repo = MonitorRepository(config.database_path)
        self._repo.initialize()
        self._rpc_urls = tuple(dict.fromkeys(config.bsc_rpc_urls or (config.bsc_rpc_url,)))
        self._rpc_index = 0
        self._rpc_backoff_until: dict[str, datetime] = {}
        self._discovery = BscPairDiscoveryClient(
            rpc_url=self._rpc_urls[self._rpc_index],
            factory_address=config.factory_address,
            quote_tokens=config.quote_tokens,
        )
        self._dexscreener = DexScreenerClient(config.dexscreener_base_url)
        self._signals = SignalEngine(config.signal)
        self._honeypot = HoneypotClient()
        self._alpha_client = BinanceAlphaClient(config.binance_alpha_url)
        self._alpha_rank_client = BinanceAlphaRankClient()
        self._binance_market_client = BinanceMarketClient()
        self._alpha_token_map: dict[str, AlphaToken] = {}
        self._binance_futures_registry: dict[str, dict[str, list[str]]] = {}
        self._alpha_refreshed_at = None
        self._discovery_retry_after = None
        self._binance_futures_refreshed_at = None
        self._binance_futures_retry_after = None
        self._holder_metrics_ran_at = None
        self._prediction_calibration: PredictionCalibration | None = None
        self._prediction_calibration_built_at: datetime | None = None
        self._alerts = None
        if config.telegram_bot_token and config.telegram_chat_id:
            self._alerts = TelegramAlertClient(config.telegram_bot_token, config.telegram_chat_id)

    def close(self) -> None:
        self._repo.close()

    def run_forever(self) -> None:
        while True:
            started = time.monotonic()
            self.run_cycle()
            elapsed = time.monotonic() - started
            sleep_for = max(1.0, self._config.worker_loop_seconds - elapsed)
            time.sleep(sleep_for)

    def run_cycle(self) -> None:
        if self._config.monitor_universe != "binance_alpha":
            archived = self._repo.archive_old_pairs(self._config.signal.max_pair_age_hours)
            if archived:
                LOGGER.info("archived %s stale pairs", archived)
        seeded = self._sync_alpha_watchlist()
        now = utcnow()
        if self._config.monitor_universe == "binance_alpha":
            due_pairs = self._repo.list_due_pairs_for_tokens(
                list(self._alpha_token_map.keys()),
                now,
                self._config.max_pairs_per_cycle,
            )
        else:
            due_pairs = self._repo.list_due_pairs(now, self._config.max_pairs_per_cycle)
        if self._config.monitor_universe == "binance_alpha":
            due_pairs = [pair for pair in due_pairs if pair["token_address"] in self._alpha_token_map]
        LOGGER.info("cycle due_pairs=%s seeded=%s", len(due_pairs), seeded)
        for pair in due_pairs:
            try:
                self._refresh_pair(pair)
            except Exception:
                LOGGER.exception("pair refresh failed for %s", pair["pair_address"])
                self._repo.schedule_pair_retry(
                    pair["pair_address"],
                    utcnow() + timedelta(seconds=self._config.signal.base_poll_interval_seconds),
                    reason="unexpected_refresh_error",
                )
        discovered = self._discover_new_pairs()
        if discovered:
            LOGGER.info("discovered %s new pairs", discovered)
        try:
            self.refresh_holder_metrics_if_due()
        except Exception:
            LOGGER.exception("holder metrics refresh failed")
        try:
            updated_outcomes = self.refresh_prediction_outcomes()
            if updated_outcomes:
                LOGGER.info("updated %s mature prediction outcomes", updated_outcomes)
        except Exception:
            LOGGER.exception("prediction outcome refresh failed")

    def _refresh_alpha_cache_if_needed(self) -> bool:
        if self._config.monitor_universe != "binance_alpha":
            return True
        if self._alpha_refreshed_at is not None:
            age = utcnow() - self._alpha_refreshed_at
            if age.total_seconds() < self._config.binance_alpha_refresh_minutes * 60 and self._alpha_token_map:
                return True
        try:
            tokens = self._alpha_client.fetch_bsc_tokens()
        except requests.RequestException:
            LOGGER.exception("failed to refresh Binance Alpha token list")
            return bool(self._alpha_token_map)
        previous_count = len(self._alpha_token_map)
        if not tokens:
            LOGGER.warning(
                "Binance Alpha token list refresh returned no BSC tokens; keeping previous cache with %s tokens",
                previous_count,
            )
            if previous_count:
                self._alpha_refreshed_at = utcnow()
            return bool(self._alpha_token_map)
        if previous_count and len(tokens) < max(1, previous_count // 2):
            LOGGER.warning(
                "Binance Alpha token list refresh returned only %s BSC tokens, below previous cache size %s; keeping previous cache",
                len(tokens),
                previous_count,
            )
            self._alpha_refreshed_at = utcnow()
            return True
        self._refresh_binance_futures_registry_if_needed()
        self._alpha_token_map = {token.token_address: token for token in tokens}
        self._alpha_refreshed_at = utcnow()
        LOGGER.info("refreshed Binance Alpha cache with %s BSC tokens", len(self._alpha_token_map))
        return True

    def _refresh_binance_futures_registry_if_needed(self) -> None:
        now = utcnow()
        refresh_seconds = max(1, self._config.binance_futures_registry_refresh_hours) * 3600
        if self._binance_futures_refreshed_at is not None:
            age = (now - self._binance_futures_refreshed_at).total_seconds()
            if age < refresh_seconds and self._binance_futures_registry:
                return
        cached = self._repo.get_external_json_cache(BINANCE_FUTURES_CACHE_KEY)
        cached_registry = cached.get("value") if cached else None
        cached_at = parse_datetime(cached.get("fetched_at")) if cached else None
        if cached_registry and cached_at is not None:
            cache_age = (now - cached_at).total_seconds()
            if cache_age < refresh_seconds:
                self._binance_futures_registry = cached_registry
                self._binance_futures_refreshed_at = cached_at
                return
        if self._binance_futures_retry_after is not None and now < self._binance_futures_retry_after:
            if cached_registry:
                self._binance_futures_registry = cached_registry
            return
        try:
            registry = self._binance_market_client.fetch_futures_registry()
        except requests.RequestException as exc:
            retry_seconds = self._external_retry_seconds(exc, default_seconds=refresh_seconds)
            self._binance_futures_retry_after = now + timedelta(seconds=retry_seconds)
            if cached_registry:
                self._binance_futures_registry = cached_registry
                LOGGER.warning("failed to refresh Binance futures registry; using cached registry")
            else:
                LOGGER.warning("failed to refresh Binance futures registry; continuing without futures labels")
            return
        self._binance_futures_registry = registry
        self._binance_futures_refreshed_at = now
        self._binance_futures_retry_after = None
        self._repo.upsert_external_json_cache(BINANCE_FUTURES_CACHE_KEY, registry, fetched_at=now)

    def _sync_alpha_watchlist(self) -> int:
        if self._config.monitor_universe != "binance_alpha":
            return 0
        if not self._refresh_alpha_cache_if_needed():
            return 0
        if not self._alpha_token_map:
            return 0
        seeded = 0
        now = utcnow()
        tokens = list(self._alpha_token_map.values())
        for alpha_token in tokens:
            self._repo.upsert_token(
                alpha_token.token_address,
                alpha_token.symbol,
                alpha_token.name,
                now,
                metadata=self._alpha_token_metadata(alpha_token),
            )
        seed_refresh_after = now - timedelta(minutes=self._config.binance_alpha_pair_seed_refresh_minutes)
        due_addresses = set(
            self._repo.list_alpha_tokens_needing_pair_seed(
                [token.token_address for token in tokens],
                now=now,
                refresh_after=seed_refresh_after,
                limit=max(1, self._config.binance_alpha_seed_batch_size),
            )
        )
        if not due_addresses:
            return 0
        tokens = [token for token in tokens if token.token_address in due_addresses]
        batch_size = max(1, min(30, self._config.binance_alpha_seed_batch_size))
        for start in range(0, len(tokens), batch_size):
            batch = tokens[start : start + batch_size]
            addresses = [token.token_address for token in batch]
            try:
                pairs_by_token = self._dexscreener.fetch_best_pairs_for_tokens(
                    chain_id=self._config.chain_id,
                    token_addresses=addresses,
                    quote_tokens=self._config.quote_tokens,
                )
            except requests.RequestException:
                LOGGER.exception("failed to seed Binance Alpha batch starting at %s", start)
                for alpha_token in batch:
                    self._repo.upsert_token(
                        alpha_token.token_address,
                        alpha_token.symbol,
                        alpha_token.name,
                        now,
                        metadata={
                            **self._alpha_token_metadata(alpha_token),
                            "pair_seed_failed_at": now.isoformat(),
                            "pair_seed_failure_reason": "dexscreener_request_failed",
                        },
                    )
                continue
            for alpha_token in batch:
                pair_info = pairs_by_token.get(alpha_token.token_address)
                if not pair_info or not pair_info.get("pair_address"):
                    self._repo.upsert_token(
                        alpha_token.token_address,
                        alpha_token.symbol,
                        alpha_token.name,
                        now,
                        metadata={
                            **self._alpha_token_metadata(alpha_token),
                            "pair_seed_failed_at": now.isoformat(),
                            "pair_seed_failure_reason": "pair_not_found",
                        },
                    )
                    continue
                self._repo.upsert_seed_pair(
                    pair_address=pair_info["pair_address"],
                    chain_id=self._config.chain_id,
                    token_address=alpha_token.token_address,
                    token_symbol=pair_info.get("token_symbol") or alpha_token.symbol,
                    token_name=pair_info.get("token_name") or alpha_token.name,
                    quote_token_address=pair_info["quote_token_address"],
                    quote_symbol=pair_info["quote_symbol"],
                    token0_address=pair_info["token0_address"],
                    token1_address=pair_info["token1_address"],
                    pair_created_at=pair_info["pair_created_at"],
                    discovered_at=now,
                    metadata={
                        "seed_source": "binance_alpha",
                        "is_binance_alpha": True,
                        "alpha_id": alpha_token.alpha_id,
                        "pair_url": pair_info.get("pair_url"),
                        "dex_id": pair_info.get("dex_id"),
                    },
                )
                self._repo.upsert_token(
                    alpha_token.token_address,
                    alpha_token.symbol,
                    alpha_token.name,
                    now,
                    metadata={
                        **self._alpha_token_metadata(alpha_token),
                        "pair_seeded_at": now.isoformat(),
                        "pair_seed_failed_at": None,
                        "pair_seed_failure_reason": None,
                    },
                )
                seeded += 1
        return seeded

    def _alpha_token_metadata(self, alpha_token: AlphaToken) -> dict:
        token_metadata = sanitize_alpha_metadata(alpha_token.metadata())
        if token_metadata.get("holder_count") is None:
            token_metadata.pop("holder_count", None)
        token_metadata.update(
            build_binance_listing_labels(
                symbol=alpha_token.symbol,
                cex_coin_name=(alpha_token.raw_payload or {}).get("cexCoinName"),
                listing_cex=(alpha_token.raw_payload or {}).get("listingCex"),
                futures_registry=self._binance_futures_registry,
            )
        )
        return token_metadata

    def _discover_new_pairs(self) -> int:
        if self._discovery_retry_after is not None and utcnow() < self._discovery_retry_after:
            return 0
        if self._config.monitor_universe == "binance_alpha" and not self._refresh_alpha_cache_if_needed():
            return 0
        if not self._ensure_discovery_endpoint_available():
            return 0
        try:
            latest_safe_block = self._discovery.latest_safe_block(self._config.discovery_block_confirmations)
        except Exception as exc:
            LOGGER.exception("failed to fetch latest safe block from RPC")
            self._handle_discovery_rpc_failure(exc)
            return 0
        cursor = self._repo.get_cursor(SCAN_CURSOR_KEY)
        if cursor is None:
            start_block = self._config.discovery_start_block
            if start_block is None:
                start_block = max(0, latest_safe_block - self._config.discovery_initial_backfill_blocks)
        else:
            start_block = cursor + 1
        if start_block > latest_safe_block:
            return 0

        discovered_count = 0
        discovery_failed = False
        chunk_size = max(1, self._config.discovery_block_chunk_size)
        for chunk_start in range(start_block, latest_safe_block + 1, chunk_size):
            chunk_end = min(chunk_start + chunk_size - 1, latest_safe_block)
            try:
                pairs = self._discovery.scan_pair_created_events(chunk_start, chunk_end)
            except Exception as exc:
                LOGGER.exception("discovery failed for block range %s-%s", chunk_start, chunk_end)
                self._handle_discovery_rpc_failure(exc)
                discovery_failed = True
                break
            for pair in pairs:
                alpha_token = self._alpha_token_map.get(pair.token_address)
                if self._config.monitor_universe == "binance_alpha" and alpha_token is None:
                    continue
                inserted = self._repo.insert_discovered_pair(pair, self._config.chain_id)
                if inserted:
                    token_symbol = alpha_token.symbol if alpha_token else None
                    token_name = alpha_token.name if alpha_token else None
                    metadata = self._alpha_token_metadata(alpha_token) if alpha_token else None
                    self._repo.upsert_token(pair.token_address, token_symbol, token_name, pair.discovered_at, metadata)
                    discovered_count += 1
            self._repo.set_cursor(SCAN_CURSOR_KEY, chunk_end)
        if discovered_count and not discovery_failed:
            self._discovery_retry_after = None
        return discovered_count

    def _schedule_discovery_retry(self, retry_seconds: int | None = None) -> None:
        retry_seconds = retry_seconds or max(60, self._config.worker_loop_seconds * 4)
        self._discovery_retry_after = utcnow() + timedelta(seconds=retry_seconds)

    def _build_discovery_client(self, rpc_url: str) -> BscPairDiscoveryClient:
        return BscPairDiscoveryClient(
            rpc_url=rpc_url,
            factory_address=self._config.factory_address,
            quote_tokens=self._config.quote_tokens,
        )

    def _ensure_discovery_endpoint_available(self) -> bool:
        now = utcnow()
        current_url = self._rpc_urls[self._rpc_index]
        if self._rpc_backoff_until.get(current_url) is None or now >= self._rpc_backoff_until[current_url]:
            return True
        for offset in range(1, len(self._rpc_urls) + 1):
            candidate_index = (self._rpc_index + offset) % len(self._rpc_urls)
            candidate_url = self._rpc_urls[candidate_index]
            retry_after = self._rpc_backoff_until.get(candidate_url)
            if retry_after is None or now >= retry_after:
                self._switch_discovery_endpoint(candidate_index)
                return True
        next_retry_at = min(self._rpc_backoff_until.values())
        self._discovery_retry_after = next_retry_at
        return False

    def _handle_discovery_rpc_failure(self, exc: Exception) -> None:
        retry_seconds = self._external_retry_seconds(
            exc,
            default_seconds=max(60, self._config.worker_loop_seconds * 4),
        )
        failed_url = self._rpc_urls[self._rpc_index]
        self._rpc_backoff_until[failed_url] = utcnow() + timedelta(seconds=retry_seconds)
        switched = False
        for offset in range(1, len(self._rpc_urls) + 1):
            candidate_index = (self._rpc_index + offset) % len(self._rpc_urls)
            candidate_url = self._rpc_urls[candidate_index]
            retry_after = self._rpc_backoff_until.get(candidate_url)
            if retry_after is None or utcnow() >= retry_after:
                self._switch_discovery_endpoint(candidate_index)
                switched = True
                break
        if switched:
            self._schedule_discovery_retry(max(1, min(retry_seconds, self._config.worker_loop_seconds)))
        else:
            self._schedule_discovery_retry(retry_seconds)

    def _switch_discovery_endpoint(self, index: int) -> None:
        if index == self._rpc_index:
            return
        self._rpc_index = index
        self._discovery = self._build_discovery_client(self._rpc_urls[index])
        LOGGER.warning("switched BSC RPC discovery endpoint to %s", self._rpc_urls[index])

    def _external_retry_seconds(self, exc: Exception, *, default_seconds: int) -> int:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
        retry_after_header = None
        if response is not None:
            retry_after_header = getattr(response, "headers", {}).get("Retry-After")
        if retry_after_header:
            try:
                return max(default_seconds, int(float(retry_after_header)))
            except ValueError:
                pass
        if status_code == 418:
            return max(default_seconds, 3600)
        if status_code == 429:
            return max(default_seconds, 300)
        return default_seconds

    def _refresh_pair(self, pair: dict) -> None:
        pair_created_at = parse_datetime(pair["pair_created_at"])
        if pair_created_at is None:
            LOGGER.warning("pair %s missing pair_created_at; skipping", pair["pair_address"])
            self._repo.schedule_pair_retry(
                pair["pair_address"],
                utcnow() + timedelta(seconds=self._config.signal.base_poll_interval_seconds),
                reason="missing_pair_created_at",
            )
            return
        try:
            snapshot = self._dexscreener.fetch_pair_snapshot(
                chain_id=self._config.chain_id,
                pair_address=pair["pair_address"],
                token_address=pair["token_address"],
                quote_token_address=pair["quote_token_address"],
                quote_symbol=pair["quote_symbol"],
                pair_created_at=pair_created_at,
            )
        except requests.RequestException as exc:
            LOGGER.warning("DexScreener fetch failed for %s: %s", pair["pair_address"], exc)
            self._repo.schedule_pair_retry(
                pair["pair_address"],
                utcnow() + timedelta(seconds=self._config.signal.base_poll_interval_seconds),
                reason=str(exc),
            )
            return

        if snapshot is None:
            LOGGER.info("pair %s not yet indexed by DexScreener", pair["pair_address"])
            self._repo.schedule_pair_retry(
                pair["pair_address"],
                utcnow() + timedelta(seconds=self._config.signal.base_poll_interval_seconds),
                reason="snapshot_not_available",
            )
            return

        alpha_token = self._alpha_token_map.get(snapshot.token_address)
        alpha_metadata = self._alpha_token_metadata(alpha_token) if alpha_token is not None else {}
        snapshot = sanitize_pair_snapshot(
            snapshot,
            monitor_universe=self._config.monitor_universe,
            alpha_reference=build_alpha_reference(alpha_metadata),
        )
        features = build_feature_vector(
            snapshot,
            self._config.signal,
            monitor_universe=self._config.monitor_universe,
        )
        current_token_metadata = self._repo.get_token_metadata(snapshot.token_address)
        token_metadata = {**current_token_metadata, "quote_symbol": snapshot.quote_symbol}
        if alpha_metadata:
            token_metadata.update(alpha_metadata)
        decision = self._signals.evaluate(
            features,
            observed_at=snapshot.observed_at,
            monitor_universe=self._config.monitor_universe,
            token_metadata=token_metadata,
        )
        self._repo.upsert_token(
            snapshot.token_address,
            snapshot.token_symbol or (alpha_token.symbol if alpha_token else None),
            snapshot.token_name or (alpha_token.name if alpha_token else None),
            snapshot.observed_at,
            metadata=token_metadata,
        )
        self._repo.insert_snapshot(snapshot, age_minutes=features.age_minutes, risk_flags=list(features.risk_flags))
        recent_history = self._repo.list_snapshot_context(
            snapshot.pair_address,
            snapshot.observed_at - timedelta(hours=72),
        )
        candidate_indicators = compute_candidate_indicators(
            observed_at=snapshot.observed_at,
            price_usd=snapshot.price_usd,
            volume_h1=snapshot.volume_h1,
            market_cap=snapshot.market_cap,
            fdv=snapshot.fdv,
            history_rows=recent_history,
        )
        decision = replace(
            decision,
            features={
                **decision.features,
                **candidate_indicators,
            },
        )
        signal_id = self._repo.insert_signal(snapshot.pair_address, snapshot.token_address, decision)
        prediction = build_prediction_result(
            decision,
            token_metadata=token_metadata,
            calibration=self._get_prediction_calibration(),
        )
        self._repo.upsert_signal_prediction(
            signal_id,
            pair_address=snapshot.pair_address,
            token_address=snapshot.token_address,
            observed_at=snapshot.observed_at,
            prediction=prediction,
        )
        if decision.pair_state == "archived":
            next_refresh_at = None
            active = False
        elif decision.pair_state in {"focused", "alerted"}:
            next_refresh_at = snapshot.observed_at + timedelta(
                seconds=self._config.signal.focus_poll_interval_seconds
            )
            active = True
        else:
            next_refresh_at = snapshot.observed_at + timedelta(
                seconds=self._config.signal.base_poll_interval_seconds
            )
            active = True
        metadata = {"pair_url": snapshot.pair_url, "token_name": snapshot.token_name}
        self._repo.update_pair_after_snapshot(
            snapshot.pair_address,
            state=decision.pair_state,
            dex_id=snapshot.dex_id or None,
            token_symbol=snapshot.token_symbol or None,
            token_name=snapshot.token_name or None,
            last_snapshot_at=snapshot.observed_at,
            next_refresh_at=next_refresh_at,
            risk_flags=list(decision.risk_flags),
            metadata=metadata,
            active=active,
        )
        self._maybe_alert(pair, snapshot, decision, signal_id)

    def refresh_prediction_outcomes(self, *, now=None, limit: int = 100) -> int:
        now = now or utcnow()
        rows = self._repo.list_predictions_needing_outcomes(now, limit=limit)
        updated = 0
        for row in rows:
            observed_at = parse_datetime(row.get("observed_at"))
            if observed_at is None:
                continue
            outcome = compute_prediction_outcome_with_hourly_ohlcv(
                self._repo,
                pair_address=row["pair_address"],
                observed_at=observed_at,
                feature_json=row.get("feature_json"),
                network=self._config.chain_id,
                now=now,
            )
            if outcome is None:
                continue
            self._repo.upsert_prediction_outcome(int(row["signal_id"]), outcome, evaluated_at=now)
            updated += 1
        if updated:
            self._prediction_calibration = None
            self._prediction_calibration_built_at = None
        return updated

    def _get_prediction_calibration(self) -> PredictionCalibration:
        now = utcnow()
        if (
            self._prediction_calibration is not None
            and self._prediction_calibration_built_at is not None
            and (now - self._prediction_calibration_built_at).total_seconds() < 300
        ):
            return self._prediction_calibration
        rows = self._repo.list_prediction_dataset_rows()
        self._prediction_calibration = build_prediction_calibration(rows)
        self._prediction_calibration_built_at = now
        if self._prediction_calibration.total_rows:
            LOGGER.info("prediction calibration loaded rows=%s", self._prediction_calibration.total_rows)
        return self._prediction_calibration

    def _maybe_alert(self, pair: dict, snapshot, decision, signal_id: int) -> None:
        if self._alerts is None or not decision.should_alert:
            return
        last_sent = self._repo.get_recent_successful_alert_at(snapshot.pair_address, "telegram")
        if last_sent is not None:
            cooldown_seconds = self._config.signal.alert_cooldown_minutes * 60
            if (snapshot.observed_at - last_sent).total_seconds() < cooldown_seconds:
                LOGGER.debug("alert cooldown active for %s", snapshot.pair_address)
                return
        try:
            message_id = self._alerts.send_signal_alert(
                token_symbol=snapshot.token_symbol or pair["token_address"][:8],
                pair_address=snapshot.pair_address,
                quote_symbol=snapshot.quote_symbol,
                decision=decision,
                metadata={"pair_url": snapshot.pair_url},
            )
        except requests.RequestException as exc:
            LOGGER.warning("telegram alert failed for %s: %s", snapshot.pair_address, exc)
            self._repo.record_alert(signal_id, channel="telegram", delivery_state="failed", error_text=str(exc))
            return
        self._repo.record_alert(
            signal_id,
            channel="telegram",
            delivery_state="sent",
            provider_message_id=message_id,
        )

    def refresh_holder_metrics_if_due(self) -> int:
        now = utcnow()
        if self._holder_metrics_ran_at is not None:
            elapsed = (now - self._holder_metrics_ran_at).total_seconds()
            if elapsed < self._config.holder_metrics_job_interval_seconds:
                return 0
        self._holder_metrics_ran_at = now
        return self.refresh_holder_metrics(now=now)

    def refresh_holder_metrics(self, *, now=None) -> int:
        now = now or utcnow()
        stale_before = now - timedelta(hours=self._config.holder_metrics_refresh_hours)
        rows = self._repo.list_tokens_needing_holder_metrics(
            stale_before=stale_before,
            limit=max(1, self._config.holder_metrics_batch_size),
        )
        try:
            alpha_rank_metrics = self._alpha_rank_client.fetch_bsc_alpha_rank()
        except requests.RequestException:
            LOGGER.warning("Binance Alpha rank lookup failed")
            alpha_rank_metrics = {}
        refreshed = 0
        for row in rows:
            token_address = row["token_address"]
            metadata = dict(row["metadata"])
            metadata["holder_metrics_attempted_at"] = now.isoformat()
            rank_metrics = alpha_rank_metrics.get(token_address) or {}
            holder_count = rank_metrics.get("holder_count")
            if holder_count is not None:
                metadata["holder_count"] = holder_count
                metadata["holder_count_source"] = "binance_alpha_rank"
            else:
                try:
                    holder_count = self._honeypot.fetch_holder_count(token_address, chain_id=56)
                except requests.RequestException:
                    LOGGER.warning("holder lookup failed for %s", token_address)
                    holder_count = None
                if holder_count is not None:
                    metadata["holder_count"] = holder_count
                    metadata["holder_count_source"] = "honeypot"

            top10_holder_share = rank_metrics.get("top10_holder_share")
            if top10_holder_share is not None:
                metadata["top10_holder_share"] = top10_holder_share
                metadata["top10_holder_source"] = "binance_alpha_rank"
            else:
                try:
                    top10_holder_share = self._honeypot.fetch_top10_holder_share(token_address, chain_id=56, top_n=10)
                except requests.RequestException:
                    LOGGER.warning("top holder lookup failed for %s", token_address)
                    top10_holder_share = None
                if top10_holder_share is not None:
                    metadata["top10_holder_share"] = top10_holder_share
                    metadata["top10_holder_source"] = "honeypot"
            if holder_count is not None or top10_holder_share is not None:
                metadata["holder_metrics_updated_at"] = now.isoformat()
                refreshed += 1
            self._repo.upsert_token(
                token_address,
                row.get("symbol"),
                row.get("name"),
                now,
                metadata=metadata,
            )
        return refreshed
