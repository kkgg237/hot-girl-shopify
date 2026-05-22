import { sqliteTable, text, integer } from "drizzle-orm/sqlite-core";

export const jobs = sqliteTable("jobs", {
  id: text("id").primaryKey(),
  referenceImageUrl: text("reference_image_url").notNull(),
  referenceImageUrls: text("reference_image_urls"),
  instructions: text("instructions").notNull(),
  createdAt: integer("created_at").notNull(),
  status: text("status").notNull().default("queued"),
  extendCanvas: integer("extend_canvas").notNull().default(0),
});

export const productJobs = sqliteTable("product_jobs", {
  id: text("id").primaryKey(),
  jobId: text("job_id")
    .notNull()
    .references(() => jobs.id),
  shopifyProductId: text("shopify_product_id").notNull(),
  title: text("title").notNull(),
  status: text("status").notNull().default("pending"),
  error: text("error"),
});

export const imageEdits = sqliteTable("image_edits", {
  id: text("id").primaryKey(),
  productJobId: text("product_job_id")
    .notNull()
    .references(() => productJobs.id),
  shopifyMediaId: text("shopify_media_id").notNull(),
  originalUrl: text("original_url").notNull(),
  editedUrl: text("edited_url"),
  status: text("status").notNull().default("pending"),
  error: text("error"),
  position: integer("position").notNull().default(0),
});

export const appSettings = sqliteTable("app_settings", {
  id: text("id").primaryKey().default("singleton"),
  houseStyleImageUrls: text("house_style_image_urls").notNull().default("[]"),
  promptRules: text("prompt_rules"),
});

export type Job = typeof jobs.$inferSelect;
export type ProductJob = typeof productJobs.$inferSelect;
export type ImageEdit = typeof imageEdits.$inferSelect;
export type Settings = typeof appSettings.$inferSelect;
