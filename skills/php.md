---
description: PHP development — Composer, Laravel, testing, built-in server
---
# PHP Development

## Composer (dependency management)

- `composer install` — install from composer.lock
- `composer require <vendor/pkg>` — add dependency
- `composer require --dev <vendor/pkg>` — add dev dependency
- `composer update` — update all deps
- `composer dump-autoload` — regenerate autoloader

Check `composer.json` with read tool for project config and scripts.

## Running PHP

- `php script.php` — run a script
- `php -S localhost:8000` — built-in development server
- `php -S localhost:8000 -t public/` — serve from public dir (common for frameworks)
- `php -l script.php` — syntax check without executing
- `php -r "echo phpversion();"` — check PHP version

## Laravel (artisan commands)

- `php artisan serve` — start dev server
- `php artisan migrate` — run migrations
- `php artisan make:model <Name> -m` — create model with migration
- `php artisan make:controller <Name>` — create controller
- `php artisan route:list` — show all routes
- `php artisan tinker` — interactive REPL (not usable in bash tool, avoid)
- `php artisan config:clear && php artisan cache:clear` — clear caches

## Testing

- `./vendor/bin/phpunit` — run PHPUnit tests
- `./vendor/bin/phpunit --filter=TestName` — run specific test
- `php artisan test` — Laravel test runner
- `php artisan test --filter=TestName` — specific test in Laravel

## Project structure

Common files to inspect with read tool:
- `composer.json` — deps and autoload config
- `.env` — environment config (never commit; do not display secrets)
- `routes/web.php`, `routes/api.php` — route definitions (Laravel)
- `config/` — framework configuration

Use bash tool for all commands. Use read/edit tools for file modifications.