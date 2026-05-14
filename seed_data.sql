--
-- PostgreSQL database dump
--

\restrict qOlQxemXbZROsNKWgXe8Q87o84J7NB7Fk9BdUwih2fGd1e7EIPZxfXtg4yNn2FQ

-- Dumped from database version 15.17
-- Dumped by pg_dump version 15.17

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Data for Name: backtest_profiles; Type: TABLE DATA; Schema: public; Owner: algovibe
--

INSERT INTO public.backtest_profiles VALUES (4, 'anglingdharma', 'scalping', '', true, 14, 12, 26, 9, 1000, 20, 2, 14, 14, 3, 3, 9, 21, 50, 14, 10, 20, 5, 14, 50, 50, 5.8, 6, 4, 35, 70, 200000000, 3.2, 2.8, 2, 2.2, 1.5, 1.5, 1, 1, 2.2, 1.5, 1.5, 0.7, 1.4, 1.3, 1.5, 0.6, 1.2, 1, 1, 1, 1.3, 0.5, 1, 1.2, 1, 1.2, 1, 1, 1, 1, 1, 1, 0.8, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1.2, 0.03, 0.045, true, true, 5, 0.02, -0.03, true, 0.012, 0.006, 10, true, 2, -0.015, 1, 0.65, 0.002111, 0.004211, 0.001111, 0.003211, 0.0021, 0.006322, 0.0085, 'taker', 100000, 10000, 10000, 5, 300, '2026-05-14 02:38:26.232058+00', '2026-05-14 02:38:26.232058+00', 1, 'scalping', 0, 0);
INSERT INTO public.backtest_profiles VALUES (1, 'Trend Pullback 5m', 'scalping', '', false, 14, 12, 26, 9, 1000, 20, 2, 14, 14, 3, 3, 9, 21, 50, 14, 10, 20, 5, 14, 50, 50, 6, 6, 4, 35, 70, 200000000, 3, 2.5, 2, 2, 1.5, 1.5, 1, 1, 2, 1.5, 1.5, 0.7, 1.4, 1.3, 1.5, 0.6, 1.2, 1, 1, 1, 1.3, 0.5, 1, 1.2, 1, 1.2, 1, 1, 1, 1, 1, 1, 0.8, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1.2, 0.03, 0.045, false, true, 5, 0.04, -0.03, false, 0.02, 0.015, 10, true, 1, -0.015, 1, 0.65, 0.002111, 0.004211, 0.001111, 0.003211, 0.0021, 0.006322, 0.0085, 'taker', 1000000, 100000, 10000, 3, 300, '2026-05-14 00:03:43.921639+00', '2026-05-14 00:03:43.921639+00', 5, 'trend_pullback', 0, 0);
INSERT INTO public.backtest_profiles VALUES (3, 'jokobodo', 'scalping', '', false, 14, 12, 26, 9, 1000, 20, 2, 14, 14, 3, 3, 9, 21, 50, 14, 10, 20, 5, 14, 50, 50, 5.5, 6, 4, 35, 70, 200000000, 3.5, 3, 2, 2.5, 1.5, 1.5, 1, 1, 2.5, 1.5, 1.5, 0.7, 1.4, 1.3, 1.5, 0.6, 1.2, 1, 1, 1, 1.3, 0.5, 1, 1.2, 1, 1.2, 1, 1, 1, 1, 1, 1, 0.8, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1.2, 0.03, 0.045, true, true, 5, 0.015, -0.04, true, 0.01, 0.005, 10, true, 3, -0.02, 1, 0.65, 0.002111, 0.004211, 0.001111, 0.003211, 0.0021, 0.006322, 0.0085, 'taker', 5000000, 200000, 10000, 10, 300, '2026-05-14 02:31:28.203584+00', '2026-05-14 02:31:28.203584+00', 1, 'scalping', 0, 0);


--
-- Name: backtest_profiles_id_seq; Type: SEQUENCE SET; Schema: public; Owner: algovibe
--

SELECT pg_catalog.setval('public.backtest_profiles_id_seq', 4, true);


--
-- PostgreSQL database dump complete
--

\unrestrict qOlQxemXbZROsNKWgXe8Q87o84J7NB7Fk9BdUwih2fGd1e7EIPZxfXtg4yNn2FQ

 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'amznxidr', 4700000, 4700000, 4700000, 4700000, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'monidr', 540.027, 540.027, 540.027, 540.027, 30);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'moonpigidr', 5.622, 5.622, 5.622, 5.622, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'novaidr', 775008, 775008, 775008, 775008, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'muraidr', 90, 90, 90, 90, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'aiidr', 427, 427, 427, 427, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'myxidr', 3891, 3891, 3891, 3891, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'neorpnewidr', 1002, 1002, 1002, 1002, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'aixbtidr', 541, 541, 541, 541, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'adaidr', 4661, 4661, 4661, 4661, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'altlayeridr', 132, 132, 132, 132, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'arcidr', 1103, 1103, 1103, 1103, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'atomidr', 35652, 35652, 35652, 35652, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'auraidr', 432, 432, 432, 432, 7741.10648146);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'bioidr', 790, 790, 790, 790, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'bnkridr', 7.505, 7.505, 7.505, 7.505, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'bomeidr', 11, 11, 11, 11, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'bonkidr', 0.120908, 0.120908, 0.120908, 0.120908, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'bridr', 2463, 2463, 2463, 2463, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'cakeidr', 26888, 26888, 26888, 26888, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'cbgidr', 10, 10, 10, 10, 30366.6);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'chtidr', 8, 8, 8, 8, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'collatidr', 37.501, 37.501, 37.501, 37.501, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'cotiidr', 258, 258, 258, 258, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'dexeidr', 213393, 213393, 213393, 213393, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'dgbidr', 66, 66, 66, 66, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'enaidr', 2070, 2070, 2070, 2070, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'ensidr', 124974, 124974, 124974, 124974, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'fancidr', 24, 24, 24, 24, 977.96296296);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'fetidr', 3761, 3761, 3761, 3761, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'fwogidr', 137, 137, 137, 137, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'h2oidr', 10, 10, 10, 10, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'humanityidr', 4535, 4535, 4535, 4535, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'honeyidr', 39, 39, 39, 39, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'hyperidr', 1852, 1852, 1852, 1852, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'hypeidr', 683000, 683000, 683000, 683000, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'indridr', 1000008, 1000008, 1000008, 1000008, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'iotaidr', 1066, 1066, 1066, 1066, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'iotxidr', 88, 88, 88, 88, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'jasmyidr', 115, 115, 115, 115, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'jstidr', 1592, 1592, 1592, 1592, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'kaitoidr', 8465, 8465, 8465, 8465, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'kdagidr', 15, 15, 15, 15, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'kiteidr', 3618, 3618, 3618, 3618, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'komaidr', 130.999, 130.999, 130.999, 130.999, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'ldoidr', 6810, 6810, 6810, 6810, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'lptidr', 38560, 38560, 38560, 38560, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'ltcidr', 1020000, 1020000, 1020000, 1020000, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'mewidr', 10.62, 10.62, 10.62, 10.62, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'mogidr', 0.002814, 0.002814, 0.002814, 0.002814, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'nxaidr', 34900, 34936, 34900, 34936, 1.19981099);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'opidr', 2545, 2545, 2545, 2545, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'orcidr', 13, 13, 13, 13, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'paxgidr', 82199987, 82199987, 82199987, 82199987, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'penguidr', 159.502, 159.502, 159.502, 159.502, 16285);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'pondidr', 41.221, 41.221, 41.221, 41.221, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'popcatidr', 1115, 1115, 1115, 1115, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'primeidr', 7206, 7206, 7206, 7206, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'pumpidr', 33.348, 33.348, 33.348, 33.348, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'pythidr', 907, 907, 907, 907, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'rayidr', 13934, 13934, 13934, 13934, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'renderidr', 32992, 32992, 32992, 32992, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'riveridr', 119171, 119171, 119171, 119171, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'renidr', 61, 61, 61, 61, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'rvmidr', 10, 10, 10, 10, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'sandidr', 1354, 1354, 1354, 1354, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'sirenidr', 20014, 20014, 20014, 20014, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'sfiidr', 1940546, 1940546, 1940546, 1940546, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'shellidr', 665, 665, 665, 665, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'shibidr', 0.110346, 0.110346, 0.110346, 0.110346, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'solayeridr', 1738, 1738, 1738, 1738, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'solidr', 1601833, 1601833, 1601833, 1601833, 0.00621725);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'somiidr', 3116, 3116, 3116, 3116, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'sqdidr', 643, 643, 643, 643, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'spxidr', 7326, 7326, 7326, 7326, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'syrupidr', 4112, 4112, 4112, 4112, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'tfuelidr', 203, 203, 203, 203, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'thetaidr', 3946, 3946, 3946, 3946, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'tmgidr', 23732, 23757, 23732, 23757, 2.99964203);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'tokenidr', 57, 57, 57, 57, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'usdtidr', 17540, 17540, 17540, 17540, 232.04495853);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'vbgidr', 10, 10, 10, 10, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'veloidr', 72, 72, 72, 72, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'vetidr', 129, 129, 129, 129, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'wealthidr', 4929065, 4929065, 4929065, 4929065, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'w3s2idr', 5305, 5310, 5305, 5310, 15.19811587);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'whitewhaleidr', 113, 113, 113, 113, 7751.69026548);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'xgdidr', 1625209, 1625209, 1625209, 1625209, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'xridr', 93, 93, 93, 93, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'yfiidr', 49855996, 49855996, 49855996, 49855996, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'zenidr', 115032, 115032, 115032, 115032, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'yfiiidr', 555556, 555556, 555556, 555556, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'zerebroidr', 587, 587, 587, 587, 6847.47129756);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'zilidr', 72, 72, 72, 72, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'zoraidr', 221.972, 221.972, 221.972, 221.972, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'zroidr', 24666, 24666, 24666, 24666, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'alifidr', 2210, 2210, 2210, 2210, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'attidr', 1, 1, 1, 1, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'ladysidr', 0.000165, 0.000165, 0.000165, 0.000165, 0);
 INSERT INTO ohlcv_1m (ts, pair, open, high, low, close, volume) VALUES ('2026-05-14 00:57:00+00', 'api3idr', 6535, 6535, 6535, 6535, 0);

