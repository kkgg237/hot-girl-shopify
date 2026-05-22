export const API_VERSION = "2025-01";

function endpoint(): string {
  const domain = process.env.SHOPIFY_STORE_DOMAIN;
  if (!domain) throw new Error("SHOPIFY_STORE_DOMAIN is not set");
  return `https://${domain}/admin/api/${API_VERSION}/graphql.json`;
}

function token(): string {
  const t = process.env.SHOPIFY_ADMIN_TOKEN;
  if (!t) throw new Error("SHOPIFY_ADMIN_TOKEN is not set");
  return t;
}

interface GraphQLError {
  message: string;
  [k: string]: unknown;
}

interface GraphQLResponse<T> {
  data?: T;
  errors?: GraphQLError[];
}

export async function gql<T>(
  query: string,
  variables: Record<string, unknown> = {}
): Promise<T> {
  const res = await fetch(endpoint(), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Shopify-Access-Token": token(),
    },
    body: JSON.stringify({ query, variables }),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Shopify HTTP ${res.status}: ${text}`);
  }
  const json = (await res.json()) as GraphQLResponse<T>;
  if (json.errors && json.errors.length) {
    throw new Error(`Shopify GraphQL: ${json.errors.map((e) => e.message).join("; ")}`);
  }
  if (!json.data) throw new Error("Shopify GraphQL: no data");
  return json.data;
}

export interface ShopifyImage {
  id: string;
  url: string;
  altText: string | null;
}

export interface ShopifyProductSummary {
  id: string;
  title: string;
  totalInventory: number | null;
  featuredImage: { url: string; altText: string | null } | null;
  images: ShopifyImage[];
}

export interface PageInfo {
  hasNextPage: boolean;
  endCursor: string | null;
}

const LIST_PRODUCTS_QUERY = `#graphql
  query ListProducts($cursor: String, $query: String) {
    products(first: 24, after: $cursor, query: $query, sortKey: UPDATED_AT, reverse: true) {
      pageInfo { hasNextPage endCursor }
      edges {
        cursor
        node {
          id
          title
          totalInventory
          featuredImage { url altText }
          images(first: 10) { nodes { id url altText } }
        }
      }
    }
  }
`;

interface ListProductsRaw {
  products: {
    pageInfo: PageInfo;
    edges: Array<{
      cursor: string;
      node: {
        id: string;
        title: string;
        totalInventory: number | null;
        featuredImage: { url: string; altText: string | null } | null;
        images: { nodes: ShopifyImage[] };
      };
    }>;
  };
}

/**
 * Two-phase paginated product list:
 *   Phase 1: products with inventory_total > 0, sorted UPDATED_AT desc
 *   Phase 2: products with inventory_total <= 0, sorted UPDATED_AT desc
 *
 * Cursor format we expose to the client encodes both phase and the underlying
 * Shopify cursor: "<phase>:<cursor>" where phase is "in" or "oos". When phase 1
 * runs out we transparently flip to phase 2 by returning a fresh "oos:" cursor.
 */
type Phase = "in" | "oos";

function parseCursor(c: string | undefined | null): { phase: Phase; cursor: string | null } {
  if (!c) return { phase: "in", cursor: null };
  const idx = c.indexOf(":");
  if (idx < 0) return { phase: "in", cursor: c };
  const phase: Phase = c.slice(0, idx) === "oos" ? "oos" : "in";
  const rest = c.slice(idx + 1);
  return { phase, cursor: rest.length > 0 ? rest : null };
}

function buildQuery(userQuery: string | undefined, phase: Phase): string {
  const inventoryClause = phase === "in" ? "inventory_total:>0" : "inventory_total:<=0";
  const u = (userQuery ?? "").trim();
  return u ? `(${u}) AND ${inventoryClause}` : inventoryClause;
}

export async function listProducts({
  cursor,
  query,
}: {
  cursor?: string;
  query?: string;
}): Promise<{ products: ShopifyProductSummary[]; pageInfo: PageInfo }> {
  const { phase, cursor: actual } = parseCursor(cursor);
  const data = await gql<ListProductsRaw>(LIST_PRODUCTS_QUERY, {
    cursor: actual,
    query: buildQuery(query, phase),
  });

  // Skip products with no images at all — there's nothing to edit.
  const products: ShopifyProductSummary[] = data.products.edges
    .map((e) => ({
      id: e.node.id,
      title: e.node.title,
      totalInventory: e.node.totalInventory ?? null,
      featuredImage: e.node.featuredImage,
      images: e.node.images.nodes,
    }))
    .filter((p) => !!p.featuredImage || p.images.length > 0);

  let nextCursor: string | null = null;
  let hasNextPage = false;

  if (data.products.pageInfo.hasNextPage && data.products.pageInfo.endCursor) {
    nextCursor = `${phase}:${data.products.pageInfo.endCursor}`;
    hasNextPage = true;
  } else if (phase === "in") {
    // Phase 1 exhausted — kick off phase 2 from the start.
    nextCursor = "oos:";
    hasNextPage = true;
  }

  return { products, pageInfo: { hasNextPage, endCursor: nextCursor } };
}

const PRODUCTS_BY_IDS_QUERY = `#graphql
  query ProductsByIds($ids: [ID!]!) {
    nodes(ids: $ids) {
      __typename
      ... on Product {
        id
        title
        featuredImage { url altText }
      }
    }
  }
`;

interface ProductsByIdsRaw {
  nodes: Array<
    | null
    | {
        __typename: string;
        id?: string;
        title?: string;
        featuredImage?: { url: string; altText: string | null } | null;
      }
  >;
}

export interface ShopifyProductLite {
  id: string;
  title: string;
  featuredImage: { url: string; altText: string | null } | null;
}

export async function productsByIds(ids: string[]): Promise<ShopifyProductLite[]> {
  if (ids.length === 0) return [];
  const data = await gql<ProductsByIdsRaw>(PRODUCTS_BY_IDS_QUERY, { ids });
  const out: ShopifyProductLite[] = [];
  for (const n of data.nodes) {
    if (n && n.__typename === "Product" && n.id && n.title) {
      out.push({
        id: n.id,
        title: n.title,
        featuredImage: n.featuredImage ?? null,
      });
    }
  }
  return out;
}

const PRODUCT_MEDIA_QUERY = `#graphql
  query ProductMedia($id: ID!) {
    product(id: $id) {
      id
      title
      media(first: 50) {
        nodes {
          __typename
          ... on MediaImage {
            id
            image { url altText }
          }
        }
      }
    }
  }
`;

interface ProductMediaRaw {
  product: {
    id: string;
    title: string;
    media: {
      nodes: Array<{
        __typename: string;
        id?: string;
        image?: { url: string; altText: string | null } | null;
      }>;
    };
  } | null;
}

export interface ProductMediaItem {
  id: string;
  url: string;
  altText: string | null;
}

export async function getProductMedia(productId: string): Promise<{
  product: { id: string; title: string };
  media: ProductMediaItem[];
}> {
  const data = await gql<ProductMediaRaw>(PRODUCT_MEDIA_QUERY, { id: productId });
  if (!data.product) throw new Error(`Product not found: ${productId}`);
  const media: ProductMediaItem[] = [];
  for (const n of data.product.media.nodes) {
    if (n.__typename === "MediaImage" && n.id && n.image?.url) {
      media.push({
        id: n.id,
        url: n.image.url,
        altText: n.image.altText ?? null,
      });
    }
  }
  return {
    product: { id: data.product.id, title: data.product.title },
    media,
  };
}

const PRODUCT_BY_HANDLE_QUERY = `#graphql
  query ProductByHandle($handle: String!) {
    productByHandle(handle: $handle) {
      id
      title
      featuredImage { url altText }
    }
  }
`;

interface ProductByHandleRaw {
  productByHandle: {
    id: string;
    title: string;
    featuredImage: { url: string; altText: string | null } | null;
  } | null;
}

/**
 * Resolve a user-entered reference into a product (id, title, featured image).
 * Accepts:
 *   - Full Shopify product URL: https://*.myshopify.com/products/<handle> or admin URL
 *   - Product handle: my-product-handle
 *   - Numeric product id: 1234567890
 *   - Full GID: gid://shopify/Product/1234567890
 */
export async function resolveProductRef(input: string): Promise<ShopifyProductLite> {
  const raw = input.trim();
  if (!raw) throw new Error("Empty reference");

  // GID
  if (raw.startsWith("gid://shopify/Product/")) {
    const found = await productsByIds([raw]);
    if (found.length === 0) throw new Error("Product not found");
    return found[0];
  }

  // URL: extract /products/<handle> or admin /products/<id>
  if (raw.startsWith("http://") || raw.startsWith("https://")) {
    let u: URL;
    try { u = new URL(raw); } catch { throw new Error("Invalid URL"); }
    // storefront URL: /products/<handle>
    const storefrontMatch = u.pathname.match(/\/products\/([^/?#]+)/);
    if (storefrontMatch) {
      const handle = decodeURIComponent(storefrontMatch[1]);
      // numeric? treat as id
      if (/^\d+$/.test(handle)) {
        const gid = `gid://shopify/Product/${handle}`;
        const found = await productsByIds([gid]);
        if (found.length === 0) throw new Error("Product not found");
        return found[0];
      }
      const data = await gql<ProductByHandleRaw>(PRODUCT_BY_HANDLE_QUERY, { handle });
      if (!data.productByHandle) throw new Error(`Product not found for handle: ${handle}`);
      return {
        id: data.productByHandle.id,
        title: data.productByHandle.title,
        featuredImage: data.productByHandle.featuredImage,
      };
    }
    throw new Error("URL doesn't look like a Shopify product link");
  }

  // Numeric id
  if (/^\d+$/.test(raw)) {
    const gid = `gid://shopify/Product/${raw}`;
    const found = await productsByIds([gid]);
    if (found.length === 0) throw new Error("Product not found");
    return found[0];
  }

  // Otherwise treat as handle
  const data = await gql<ProductByHandleRaw>(PRODUCT_BY_HANDLE_QUERY, { handle: raw });
  if (!data.productByHandle) throw new Error(`Product not found for handle: ${raw}`);
  return {
    id: data.productByHandle.id,
    title: data.productByHandle.title,
    featuredImage: data.productByHandle.featuredImage,
  };
}

const STAGED_UPLOAD_CREATE_MUTATION = `#graphql
  mutation StagedUploadsCreate($input: [StagedUploadInput!]!) {
    stagedUploadsCreate(input: $input) {
      stagedTargets {
        url
        resourceUrl
        parameters { name value }
      }
      userErrors { field message }
    }
  }
`;

export interface StagedTarget {
  url: string;
  resourceUrl: string;
  parameters: Array<{ name: string; value: string }>;
}

interface StagedUploadCreateRaw {
  stagedUploadsCreate: {
    stagedTargets: StagedTarget[];
    userErrors: Array<{ field: string[] | null; message: string }>;
  };
}

export async function stagedUploadCreate(
  filename: string,
  mimeType: string,
  fileSize: number
): Promise<StagedTarget> {
  const data = await gql<StagedUploadCreateRaw>(STAGED_UPLOAD_CREATE_MUTATION, {
    input: [
      {
        filename,
        mimeType,
        httpMethod: "POST",
        resource: "IMAGE",
        fileSize: String(fileSize),
      },
    ],
  });
  const errs = data.stagedUploadsCreate.userErrors;
  if (errs.length) {
    throw new Error(`stagedUploadsCreate: ${errs.map((e) => e.message).join("; ")}`);
  }
  const target = data.stagedUploadsCreate.stagedTargets[0];
  if (!target) throw new Error("stagedUploadsCreate returned no targets");
  return target;
}

export async function uploadToStaged(target: StagedTarget, buffer: Buffer): Promise<void> {
  const form = new FormData();
  for (const p of target.parameters) {
    form.append(p.name, p.value);
  }
  // Copy into a fresh ArrayBuffer to satisfy strict BlobPart typing.
  const ab = new ArrayBuffer(buffer.byteLength);
  new Uint8Array(ab).set(buffer);
  form.append("file", new Blob([ab]));

  const res = await fetch(target.url, { method: "POST", body: form });
  if (!res.ok && res.status !== 201 && res.status !== 204) {
    const text = await res.text().catch(() => "");
    throw new Error(`Staged upload failed ${res.status}: ${text}`);
  }
}

const PRODUCT_CREATE_MEDIA_MUTATION = `#graphql
  mutation ProductCreateMedia($productId: ID!, $media: [CreateMediaInput!]!) {
    productCreateMedia(productId: $productId, media: $media) {
      media {
        ... on MediaImage { id }
      }
      mediaUserErrors { field message }
    }
  }
`;

interface ProductCreateMediaRaw {
  productCreateMedia: {
    media: Array<{ id?: string }>;
    mediaUserErrors: Array<{ field: string[] | null; message: string }>;
  };
}

export async function productCreateMedia(
  productId: string,
  resourceUrl: string,
  alt: string | null
): Promise<string> {
  const data = await gql<ProductCreateMediaRaw>(PRODUCT_CREATE_MEDIA_MUTATION, {
    productId,
    media: [
      {
        originalSource: resourceUrl,
        mediaContentType: "IMAGE",
        alt: alt ?? undefined,
      },
    ],
  });
  const errs = data.productCreateMedia.mediaUserErrors;
  if (errs.length) {
    throw new Error(`productCreateMedia: ${errs.map((e) => e.message).join("; ")}`);
  }
  const id = data.productCreateMedia.media[0]?.id;
  if (!id) throw new Error("productCreateMedia returned no media id");
  return id;
}

const PRODUCT_DELETE_MEDIA_MUTATION = `#graphql
  mutation ProductDeleteMedia($productId: ID!, $mediaIds: [ID!]!) {
    productDeleteMedia(productId: $productId, mediaIds: $mediaIds) {
      deletedMediaIds
      mediaUserErrors { field message }
    }
  }
`;

interface ProductDeleteMediaRaw {
  productDeleteMedia: {
    deletedMediaIds: string[] | null;
    mediaUserErrors: Array<{ field: string[] | null; message: string }>;
  };
}

export async function productDeleteMedia(
  productId: string,
  mediaIds: string[]
): Promise<string[]> {
  if (mediaIds.length === 0) return [];
  const data = await gql<ProductDeleteMediaRaw>(PRODUCT_DELETE_MEDIA_MUTATION, {
    productId,
    mediaIds,
  });
  const errs = data.productDeleteMedia.mediaUserErrors;
  if (errs.length) {
    throw new Error(`productDeleteMedia: ${errs.map((e) => e.message).join("; ")}`);
  }
  return data.productDeleteMedia.deletedMediaIds ?? [];
}
