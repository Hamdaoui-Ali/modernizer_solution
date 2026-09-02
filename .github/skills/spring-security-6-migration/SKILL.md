---
name: spring-security-6-migration
description: Reviews Spring Security 6 migration evidence involving SecurityFilterChain, authorizeHttpRequests, and requestMatchers.
---

# Spring Security 6 Migration

Use this skill for Spring Security migration evidence.

Review especially:

- `WebSecurityConfigurerAdapter` replacement with bean-based `SecurityFilterChain`.
- `authorizeHttpRequests` and `requestMatchers` migration details.
- JWT/resource-server, filter, CORS, CSRF, and keystore behavior.
- Any added `permitAll` or changed authorization default.

Security behavior changes require human review. Do not weaken security to pass startup.
