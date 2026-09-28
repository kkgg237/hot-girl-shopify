"""Streamlit Web UI Tab for Buyee Auto-Bidding & Smooth Pricing Curve Management.

Provides:
- 1-Click Sourcing Userscript for Buyee (Push to Queue with 0 manual typing)
- Zero-Copy Chrome CDP Tab Fetcher
- Interactive review table with Smooth Pricing Curve calculated bids
- Smooth Pricing Curve levers (Aggressiveness k, Taper gamma, Category Boosts)
- Dry-run and Live bid execution controls
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

import streamlit as st

from .cdp import fetch_open_buyee_tabs
from .pricing import calculate_smooth_bid, load_pricing_config, save_pricing_config
from .bidder import process_batch_bids, scrape_item_details, infer_category_from_title_or_breadcrumbs


def render_buyee_bidding_tab() -> None:
    """Render the full Buyee Bidding & Smooth Strategy Curve tab in Streamlit."""
    st.header("🎯 Buyee Automated Bidding & Strategy Dashboard")
    st.caption("Sift auctions with zero manual entry, review continuous curve-calculated bids, and submit in bulk.")

    cfg = load_pricing_config()
    sub_tab1, sub_tab2 = st.tabs(["📋 Batch Bidding & Review", "📈 Smooth Pricing Curve Settings"])

    # Initialize queue in session state
    if "buyee_sourcing_queue" not in st.session_state:
        st.session_state["buyee_sourcing_queue"] = []

    # -------------------------------------------------------------------------
    # SUB-TAB 1: BATCH BIDDING & REVIEW
    # -------------------------------------------------------------------------
    with sub_tab1:
        st.subheader("1. Zero-Manual-Entry Sourcing")

        with st.expander("⚡ 1-Click Buyee Browser Button (Zero Manual Typing)", expanded=True):
            st.markdown(
                "**How to eliminate all manual typing while browsing Buyee:**\n\n"
                "1. Drag this button to your Chrome Bookmarks Bar: "
                f'<a href="javascript:(function(){{var priceEl=document.querySelector(\'.g-price__number, .price, .item_price, td:has-text(\\\'YEN\\\')\'); var titleEl=document.querySelector(\'h1.g-pageTitle, h1.item_title, h1\'); var p=priceEl?priceEl.innerText.replace(/[^0-9]/g,\'\'):\'28000\'; var t=titleEl?titleEl.innerText.trim():document.title; var u=location.href; var q=JSON.parse(localStorage.getItem(\'buyee_queue\')||\'[]\'); q.push({{url:u, title:t, price:parseInt(p)||28000}}); localStorage.setItem(\'buyee_queue\', JSON.stringify(q)); navigator.clipboard.writeText(JSON.stringify(q)); alert(\'🎯 Added to Sourcing Queue! (Total: \' + q.length + \' items). Paste into Dashboard with 1-click.\');}})();" style="display:inline-block; background-color:#ff4b4b; color:white; padding:8px 16px; border-radius:6px; text-decoration:none; font-weight:bold;">🎯 1-Click Add to Buyee Queue</a>\n\n'
                "2. While inspecting photos on any Buyee item page, click **🎯 1-Click Add to Buyee Queue**.\n"
                "3. It automatically reads the **Live Title**, **Live Start Price**, and **URL** directly from the page with 0 manual typing!"
            , unsafe_allow_html=True)

        col_cdp1, col_cdp2 = st.columns(2)
        with col_cdp1:
            if st.button("🔍 Auto-Fetch Open Buyee Tabs from Chrome (CDP)", type="primary"):
                success, tabs, msg = fetch_open_buyee_tabs(port=9222)
                if success and tabs:
                    st.success(msg)
                    for t in tabs:
                        st.session_state["buyee_sourcing_queue"].append({
                            "url": t["url"],
                            "title": t["title"],
                            "price": 28000,
                        })
                else:
                    st.info(msg)

        with col_m2 if 'col_m2' in locals() else col_cdp2:
            raw_json_input = st.text_input("Paste 1-Click Queue Data (from Bookmarklet):", placeholder='[{"url": "...", "price": 28000}]')
            if raw_json_input:
                try:
                    items = json.loads(raw_json_input)
                    if isinstance(items, list):
                        st.session_state["buyee_sourcing_queue"] = items
                        st.success(f"Loaded {len(items)} items into queue!")
                except Exception:
                    pass

        # -------------------------------------------------------------------------
        # REVIEW TABLE
        # -------------------------------------------------------------------------
        queue = st.session_state.get("buyee_sourcing_queue", [])
        
        # Also allow URL text area fallback
        urls_input = st.text_area(
            "Quick URL Batch Input (optional):",
            value="",
            height=80,
            placeholder="https://buyee.jp/btob/item/25/2026072410001159",
            key="buyee_urls_input_area",
        )
        pasted_urls = [u.strip() for u in urls_input.split("\n") if u.strip() and ("buyee.jp/item" in u or "buyee.jp/btob/item" in u)]

        if pasted_urls and not queue:
            for u in pasted_urls:
                queue.append({"url": u, "title": "Buyee Auction Item", "price": 28000})

        if queue:
            st.subheader("2. Review & Confirm Calculated Bids")
            
            reviewed_items = []
            for item in queue:
                u = item.get("url", "")
                title = item.get("title", "Buyee Item")
                sp = int(item.get("price", 28000))
                
                cat = infer_category_from_title_or_breadcrumbs(title, u)
                if cat == "Other":
                    cat = "Bags"

                calc = calculate_smooth_bid(sp, cat, k=cfg.get("k"), gamma=cfg.get("gamma"), category_boosts=cfg.get("category_boosts"))

                reviewed_items.append({
                    "Include": True,
                    "Item Title": title[:45],
                    "URL": u,
                    "Category": cat,
                    "Start Price (JPY)": sp,
                    "Markup %": f"+{calc['markup_pct']}%",
                    "Increment (JPY)": calc["increment"],
                    "Max Bid (JPY)": calc["calculated_max_bid"],
                })

            edited_df = st.data_editor(
                reviewed_items,
                column_config={
                    "Include": st.column_config.CheckboxColumn("Bid?", default=True),
                    "Item Title": st.column_config.TextColumn("Item Title"),
                    "URL": st.column_config.LinkColumn("Auction URL"),
                    "Category": st.column_config.SelectboxColumn("Category", options=["Bags", "Shoes", "Watches", "Clothing", "Other"]),
                    "Start Price (JPY)": st.column_config.NumberColumn("Start Price (JPY)", format="%d JPY", min_value=1000, step=1000),
                    "Markup %": st.column_config.TextColumn("Curve Markup %"),
                    "Increment (JPY)": st.column_config.NumberColumn("Increment", format="+%d JPY"),
                    "Max Bid (JPY)": st.column_config.NumberColumn("Calculated Max Bid", format="%d JPY", min_value=0, step=1000),
                },
                num_rows="dynamic",
                width="stretch",
                key="buyee_bidding_editor",
            )

            active_bids = [row for row in edited_df if row.get("Include") and row.get("Max Bid (JPY)", 0) > 0]
            total_exposure = sum([row.get("Max Bid (JPY)", 0) for row in active_bids])

            col_exp1, col_exp2 = st.columns(2)
            col_exp1.metric("Active Items to Bid", f"{len(active_bids)} / {len(edited_df)}")
            col_exp2.metric("Total Potential Exposure", f"{total_exposure:,} JPY")

            st.subheader("3. Execute Bids")
            dry_run = st.checkbox("Dry Run mode (Simulate bidding without clicking final submit)", value=True)

            col_sub1, col_sub2 = st.columns([2, 1])
            with col_sub1:
                if st.button("🚀 Submit Bids to Buyee", type="primary"):
                    if not active_bids:
                        st.warning("No active items selected for bidding.")
                    else:
                        batch_pairs = [{"url": row["URL"], "bid_amount": row["Max Bid (JPY)"]} for row in active_bids]
                        with st.spinner(f"Processing {len(batch_pairs)} bids on Buyee..."):
                            results = process_batch_bids(batch_pairs, dry_run=dry_run, headless=True)
                            st.session_state["buyee_bid_results"] = results

            with col_sub2:
                if st.button("🗑️ Clear Queue"):
                    st.session_state["buyee_sourcing_queue"] = []
                    st.rerun()

            if "buyee_bid_results" in st.session_state:
                st.subheader("Execution Results")
                for r in st.session_state["buyee_bid_results"]:
                    status = r.get("status")
                    msg = r.get("message")
                    url = r.get("url")
                    if status in ["success", "dry_run_success"]:
                        st.success(f"[{status.upper()}] {url} -> {msg}")
                    elif status == "skipped":
                        st.info(f"[SKIPPED] {url} -> {msg}")
                    else:
                        st.error(f"[FAILED] {url} -> {msg}")

    with sub_tab2:
        st.subheader("Smooth Pricing Curve Controls (3 Main Levers)")
        st.markdown(
            "Formula: Max Bid = Round_1000( Start_Price + (1 + C) * k * Start_Price^gamma )\n\n"
            "This continuous curve replaces static price buckets—tapering high-ticket items naturally and eliminating price cliffs."
        )

        col_l1, col_l2 = st.columns(2)
        with col_l1:
            k_val = st.slider(
                "Lever 1: Base Aggressiveness (k)",
                min_value=1.0,
                max_value=6.0,
                value=float(cfg.get("k", 3.5)),
                step=0.1,
                help="Shifts the entire pricing curve up or down. Conservative=2.5, Standard=3.5, Aggressive=4.5",
            )

        with col_l2:
            gamma_val = st.slider(
                "Lever 2: High-Ticket Safety Taper (gamma)",
                min_value=0.50,
                max_value=0.90,
                value=float(cfg.get("gamma", 0.70)),
                step=0.01,
                help="Controls how fast the markup percentage decreases on expensive items. Default 0.70.",
            )

        st.markdown("#### Lever 3: Category Competition Boost (C)")
        st.caption("Percentage boost added to competitive categories vs. baseline.")

        current_boosts = cfg.get("category_boosts", {"Bags": 0.50, "Shoes": 0.25, "Watches": 0.25, "Clothing": 0.00, "Other": 0.15})
        new_boosts = {}

        cols_boost = st.columns(len(current_boosts))
        for idx, (cat_name, b_val) in enumerate(current_boosts.items()):
            with cols_boost[idx % len(cols_boost)]:
                boost_pct = st.number_input(
                    f"{cat_name} Boost (%)",
                    min_value=0,
                    max_value=200,
                    value=int(b_val * 100),
                    step=5,
                    key=f"boost_in_{cat_name}",
                )
                new_boosts[cat_name] = boost_pct / 100.0

        st.markdown("#### Real-Time Curve Preview Sample")
        sample_rows = []
        for s_sp, s_cat in [(4000, "Clothing"), (12000, "Shoes"), (28000, "Bags"), (80000, "Bags")]:
            res = calculate_smooth_bid(s_sp, s_cat, k=k_val, gamma=gamma_val, category_boosts=new_boosts)
            sample_rows.append({
                "Sample Item": f"{s_cat} @ {s_sp:,} JPY",
                "Category Boost": f"+{int(res['category_boost']*100)}%",
                "Calculated Increment": f"+{res['increment']:,} JPY",
                "Effective Markup %": f"+{res['markup_pct']}%",
                "Calculated Max Bid": f"{res['calculated_max_bid']:,} JPY",
            })
        st.dataframe(sample_rows, width="stretch")

        if st.button("💾 Save Smooth Pricing Curve Configuration"):
            new_cfg = {
                "k": k_val,
                "gamma": gamma_val,
                "category_boosts": new_boosts,
            }
            save_pricing_config(new_cfg)
            st.success("Smooth pricing curve configuration successfully saved to disk!")
