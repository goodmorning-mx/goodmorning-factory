# Frontend profiles

GoodMorning modules (gm-*) provide reusable product capabilities such as
authentication, agents, billing, MCP, and PWA behavior. A frontend profile is
a separate product-level deployment choice: it selects which application
surface Factory composes from the template.

frontend_profile accepts three values:

- react (default): creates the existing React + Vite application from
  /frontend, served by Nginx on port 80.
- next: creates only the Next.js SEO/SSR application from /seo, served by
  Node on port 3000.
- hybrid: creates both /frontend and /seo. The React application remains the
  primary public application and Factory emits routing metadata for a gateway
  to forward /seo paths to the Next.js application.

All profiles keep the API at api.{slug}.goodmorning.mx and keep the primary
frontend at {slug}.goodmorning.mx (or the dev. equivalent). Factory never
creates an app subdomain. The hybrid routing object is declarative metadata;
this goal does not implement the gateway runtime.

If a manifest omits frontend_profile, Factory normalizes it to react. This
keeps Ballet, HelloWorld, CV, and every other existing product on the current
React path until a product explicitly changes its manifest.

A request can select a profile explicitly:

{
  "product_name": "customer-portal",
  "description": "Customer portal",
  "frontend_profile": "hybrid",
  "confirm_provision": true
}

The profile is copied into both development and production catalog manifests.
Changing an existing product from react to hybrid is an explicit catalog
change requiring review; Factory does not migrate existing products
automatically.
