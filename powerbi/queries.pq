// =====================================================================
// Power Query (M) for Competitor Price Watch, reading from Supabase.
// Each block below is ONE query. In Power BI Desktop:
//   Home > Transform data > New Source > Blank Query > Advanced Editor,
//   paste the block, then rename the query to the name in the header.
// Use OPTION A (PostgreSQL connector) or OPTION B (Supabase web API), not both.
// See powerbi/SETUP.md for the full walkthrough.
// =====================================================================


// #####################################################################
// OPTION A: PostgreSQL connector (simplest; try this first)
// Credentials when asked: "Database", user = postgres.<project-ref>, password = your DB password.
// If the Power BI Service later says scheduled refresh needs a gateway, switch to OPTION B.
// #####################################################################

// ---------------------------------------------------------------------
// Query name: SupabaseHost        (Parameter, type Text)
// The Session pooler host and port from Supabase: Connect -> Session pooler, e.g.
//   aws-0-eu-central-1.pooler.supabase.com:5432
// ---------------------------------------------------------------------
"aws-0-REGION.pooler.supabase.com:5432" meta [IsParameterQuery = true, Type = "Text", IsParameterQueryRequired = true]

// ---------------------------------------------------------------------
// Query name: fnView              (function: one reporting view)
// ---------------------------------------------------------------------
(view as text) as table =>
let
    Source = PostgreSQL.Database(SupabaseHost, "postgres"),
    Data = Source{[Schema = "public", Item = view]}[Data]
in
    Data


// #####################################################################
// OPTION B: Supabase web API (scheduled refresh works in the Power BI Service without a gateway)
// Needs two parameters and the function below; then the same table queries as option A.
// Supabase: Project settings -> API: copy the Project URL and the "anon" public key.
// The anon key can ONLY read the v_* reporting views (the tables are locked), never change data.
// Also in Supabase: Project settings -> API -> "Max rows": set to 100000 (fewer, faster requests).
// #####################################################################

// ---------------------------------------------------------------------
// Query name: SupabaseUrl         (Parameter, type Text), e.g. https://abcdefgh.supabase.co
// ---------------------------------------------------------------------
"https://YOUR-PROJECT.supabase.co" meta [IsParameterQuery = true, Type = "Text", IsParameterQueryRequired = true]

// ---------------------------------------------------------------------
// Query name: SupabaseKey         (Parameter, type Text): the anon public key
// ---------------------------------------------------------------------
"PASTE-ANON-KEY" meta [IsParameterQuery = true, Type = "Text", IsParameterQueryRequired = true]

// ---------------------------------------------------------------------
// Query name: fnView              (OPTION B version: replaces option A's fnView)
// Reads a view page by page (ordered by its key columns so pages never overlap).
// ---------------------------------------------------------------------
(view as text) as table =>
let
    OrderBy = if view = "v_daily_prices" then "product_id,date"
              else if view = "v_price_periods" then "product_id,valid_from"
              else if view = "v_price_changes" then "product_id,date,change_order"
              else if view = "v_promotions" then "promo_id"
              else if view = "v_scraper_runs" then "started_at"
              else "product_id",
    PageSize = 100000,      // must not exceed Supabase's "Max rows" setting
    GetPage = (offset as number) as list =>
        Json.Document(Web.Contents(SupabaseUrl, [
            RelativePath = "rest/v1/" & view,
            Query = [select = "*", order = OrderBy, offset = Text.From(offset), limit = Text.From(PageSize)],
            Headers = [apikey = SupabaseKey, Authorization = "Bearer " & SupabaseKey, Accept = "application/json"]
        ])),
    Pages = List.Generate(
        () => [offset = 0, rows = GetPage(0)],
        each List.Count([rows]) > 0,
        each [offset = [offset] + List.Count([rows]),
              rows = if List.Count([rows]) < PageSize then {} else GetPage([offset] + List.Count([rows]))],
        each [rows]),
    Rows = List.Combine(Pages),
    Table = if List.IsEmpty(Rows) then #table({}, {}) else Table.FromRecords(Rows, null, MissingField.UseNull)
in
    Table


// #####################################################################
// TABLE QUERIES (same for option A and B)
// #####################################################################

// ---------------------------------------------------------------------
// Query name: Products
// ---------------------------------------------------------------------
let
    Source = fnView("v_products"),
    Typed = Table.TransformColumnTypes(Source, {
        {"product_id", Int64.Type}, {"competitor", type text}, {"sku", type text}, {"name", type text},
        {"category", type text}, {"site_category", type text}, {"site_category_1", type text},
        {"site_category_2", type text}, {"site_category_3", type text}, {"product_group", type text},
        {"item", type text}, {"key_item", type text}, {"currency", type text}, {"url", type text},
        {"first_seen", type date}, {"last_seen", type date}, {"days_tracked", Int64.Type}, {"search_text", type text}
    }, "en-US")
in
    Typed

// ---------------------------------------------------------------------
// Query name: PricePeriods        (FULL history, compact: one row per unchanged-price period)
// Relate to Products only (not to Date); the "... On Date" measures use it for long-range charts.
// ---------------------------------------------------------------------
let
    Source = fnView("v_price_periods"),
    Typed = Table.TransformColumnTypes(Source, {
        {"product_id", Int64.Type}, {"valid_from", type date}, {"valid_to", type date},
        {"normal_price", type number}, {"promo_price", type number}, {"effective_price", type number},
        {"on_promo", Int64.Type}, {"in_stock", Int64.Type}
    }, "en-US")
in
    Typed

// ---------------------------------------------------------------------
// Query name: DailyPrices         (last 21 days by default, one row per product per day)
// ---------------------------------------------------------------------
let
    Source = fnView("v_daily_prices"),
    Typed = Table.TransformColumnTypes(Source, {
        {"product_id", Int64.Type}, {"date", type date}, {"normal_price", type number}, {"promo_price", type number},
        {"effective_price", type number}, {"on_promo", Int64.Type}, {"promo_discount_pct", type number}, {"in_stock", Int64.Type}
    }, "en-US")
in
    Typed

// ---------------------------------------------------------------------
// Query name: PriceChanges        (full history)
// ---------------------------------------------------------------------
let
    Source = fnView("v_price_changes"),
    Typed = Table.TransformColumnTypes(Source, {
        {"product_id", Int64.Type}, {"date", type date}, {"change_type", type text}, {"change_label", type text},
        {"change_order", Int64.Type}, {"from_price", type number}, {"to_price", type number}, {"change_pct", type number}
    }, "en-US")
in
    Typed

// ---------------------------------------------------------------------
// Query name: Promotions          (one row per promo episode, full history)
// ---------------------------------------------------------------------
let
    Source = fnView("v_promotions"),
    Typed = Table.TransformColumnTypes(Source, {
        {"promo_id", Int64.Type}, {"product_id", Int64.Type}, {"promo_no", Int64.Type},
        {"start_date", type date}, {"last_promo_date", type date}, {"ended_on", type date},
        {"days_on_promo", Int64.Type}, {"ongoing", type text}, {"started_before_tracking", type text},
        {"normal_price_at_start", type number}, {"lowest_promo_price", type number},
        {"avg_discount_pct", type number}, {"max_discount_pct", type number},
        {"days_since_previous_promo", Int64.Type}, {"promo_period", type text},
        {"start_weekday", type text}, {"start_weekday_no", Int64.Type}, {"discount_band", type text}
    }, "en-US")
in
    Typed

// ---------------------------------------------------------------------
// Query name: ScraperRuns
// ---------------------------------------------------------------------
let
    Source = fnView("v_scraper_runs"),
    Typed = Table.TransformColumnTypes(Source, {
        {"competitor", type text}, {"started_at", type datetimezone}, {"finished_at", type datetimezone},
        {"status", type text}, {"items_seen", Int64.Type}, {"items_changed", Int64.Type}, {"error", type text}
    }, "en-US")
in
    Typed
