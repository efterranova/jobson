<?php
/**
 * Plugin Name: JobsOn REST Bridge for WP Job Manager
 * Description: Expone meta WP Job Manager + Cariera al REST y añade upsert idempotente por dedupe_key. Asigna taxonomías por slug (no crea nuevas) y crea/matchea employer users por display_name para que el job aparezca con la empresa correcta.
 * Version:     1.2.0
 * Author:      JobsOn
 *
 * Instala este archivo en wp-content/mu-plugins/jobson-rest.php
 * (crea la carpeta mu-plugins si no existe). No requiere activación.
 */

if (!defined('ABSPATH')) {
    exit;
}

add_action('init', function () {
    if (!post_type_exists('job_listing')) {
        return;
    }

    $auth = function () {
        return current_user_can('edit_posts');
    };

    $meta_fields = [
        '_job_location'        => 'string',
        '_application'         => 'string',
        '_company_name'        => 'string',
        '_company_website'     => 'string',
        '_company_tagline'     => 'string',
        '_company_twitter'     => 'string',
        '_company_video'       => 'string',
        '_company_logo'        => 'string',
        '_job_expires'         => 'string',
        '_remote_position'     => 'boolean',
        '_featured'            => 'boolean',
        '_filled'              => 'boolean',
        '_job_salary'          => 'string',
        '_job_salary_currency' => 'string',
        '_job_salary_unit'     => 'string',
        '_job_cover_image'     => 'string',
        '_jobson_dedupe_key'   => 'string',
        '_jobson_source_url'   => 'string',
        '_jobson_source_type'  => 'string',
        '_jobson_test_batch'   => 'string',
        '_jobson_unmatched_terms' => 'string',
    ];

    foreach ($meta_fields as $key => $type) {
        register_post_meta('job_listing', $key, [
            'show_in_rest'  => true,
            'single'        => true,
            'type'          => $type,
            'auth_callback' => $auth,
        ]);
    }
});

/**
 * Endpoint custom: POST /wp-json/jobson/v1/upsert
 * Body JSON: { dedupe_key, title, content, status?, meta{}, taxonomies{job-types[], job-categories[]} }
 * Si ya existe un job_listing con _jobson_dedupe_key igual, lo actualiza. Si no, lo crea.
 */
add_action('rest_api_init', function () {
    register_rest_route('jobson/v1', '/upsert', [
        'methods'             => 'POST',
        'permission_callback' => function () {
            return current_user_can('publish_posts') || current_user_can('edit_posts');
        },
        'callback'            => 'jobson_rest_upsert',
    ]);

    register_rest_route('jobson/v1', '/find', [
        'methods'             => 'GET',
        'permission_callback' => function () {
            return current_user_can('edit_posts');
        },
        'callback'            => function (WP_REST_Request $req) {
            $key = sanitize_text_field((string) $req->get_param('dedupe_key'));
            if ($key === '') {
                return new WP_Error('missing_key', 'dedupe_key requerido', ['status' => 400]);
            }
            $post_id = jobson_find_by_dedupe($key);
            if (!$post_id) {
                return rest_ensure_response(['found' => false]);
            }
            return rest_ensure_response([
                'found'    => true,
                'id'       => $post_id,
                'link'     => get_permalink($post_id),
                'edit'     => get_edit_post_link($post_id, 'raw'),
                'status'   => get_post_status($post_id),
            ]);
        },
    ]);
});

/**
 * Busca un user con role=employer y display_name = $name. Si no existe, lo crea.
 * Retorna user_id (0 si nombre vacío o falla).
 *
 * Para usuarios creados aquí: username `linkedin_<slug>`, email `no-reply+<slug>@erecruit.ca`,
 * role `employer`, password aleatoria, user_meta `_jobson_imported=1`.
 */
function jobson_find_or_create_employer(string $name): int {
    $name = trim($name);
    if ($name === '') return 0;

    global $wpdb;
    // 1) Match exacto por display_name (case-insensitive)
    $existing = $wpdb->get_var($wpdb->prepare(
        "SELECT ID FROM {$wpdb->users} WHERE LOWER(display_name) = LOWER(%s) ORDER BY ID ASC LIMIT 1",
        $name
    ));
    if ($existing) return (int) $existing;

    // 2) Crear
    $slug = sanitize_title($name);
    if ($slug === '') $slug = 'empresa-' . wp_generate_password(6, false, false);

    $username = 'linkedin_' . $slug;
    $i = 1;
    while (username_exists($username) && $i < 50) {
        $username = 'linkedin_' . $slug . '_' . (++$i);
    }
    if (username_exists($username)) return 0;

    $email = "no-reply+{$slug}@erecruit.ca";
    if (email_exists($email)) {
        $email = "no-reply+{$slug}-" . wp_generate_password(4, false, false) . "@erecruit.ca";
    }

    $user_id = wp_create_user($username, wp_generate_password(24), $email);
    if (is_wp_error($user_id)) {
        error_log('[jobson] no se pudo crear employer ' . $name . ': ' . $user_id->get_error_message());
        return 0;
    }

    // Setear display_name y role
    wp_update_user(['ID' => $user_id, 'display_name' => $name, 'first_name' => $name]);
    $user = new WP_User($user_id);
    if (get_role('employer')) {
        $user->set_role('employer');
    } else {
        $user->set_role('author'); // fallback si la extensión Companies no está activa
    }

    update_user_meta($user_id, '_jobson_imported', 1);
    update_user_meta($user_id, '_jobson_imported_at', current_time('mysql'));
    update_user_meta($user_id, '_jobson_source', 'linkedin');

    return (int) $user_id;
}

function jobson_find_by_dedupe(string $dedupe_key): int {
    $q = new WP_Query([
        'post_type'      => 'job_listing',
        'post_status'    => ['publish', 'draft', 'pending', 'expired', 'preview'],
        'posts_per_page' => 1,
        'fields'         => 'ids',
        'meta_query'     => [[
            'key'   => '_jobson_dedupe_key',
            'value' => $dedupe_key,
        ]],
        'no_found_rows'  => true,
    ]);
    return $q->have_posts() ? (int) $q->posts[0] : 0;
}

/**
 * Resuelve un término existente por slug o, en su defecto, por nombre exacto.
 * NUNCA crea términos nuevos. Devuelve term_id (int) o 0 si no encontró.
 */
function jobson_resolve_term(string $value, string $taxonomy): int {
    $value = trim($value);
    if ($value === '') return 0;

    // 1) Por slug (sanitizado)
    $slug = sanitize_title($value);
    $t = get_term_by('slug', $slug, $taxonomy);
    if ($t && !is_wp_error($t)) return (int) $t->term_id;

    // 2) Por nombre exacto
    $t = get_term_by('name', $value, $taxonomy);
    if ($t && !is_wp_error($t)) return (int) $t->term_id;

    return 0;
}

/**
 * Fallbacks por idioma cuando ninguna categoría resuelve.
 */
function jobson_category_fallback_id(string $lang): int {
    $slug = ($lang === 'en') ? 'others-general-category' : 'otros-categoria-general';
    $t = get_term_by('slug', $slug, 'job_listing_category');
    return ($t && !is_wp_error($t)) ? (int) $t->term_id : 0;
}

function jobson_rest_upsert(WP_REST_Request $req) {
    $params     = $req->get_json_params() ?: [];
    $dedupe_key = isset($params['dedupe_key']) ? sanitize_text_field($params['dedupe_key']) : '';
    $title      = isset($params['title']) ? wp_strip_all_tags((string) $params['title']) : '';
    $content    = isset($params['content']) ? wp_kses_post((string) $params['content']) : '';
    $status     = isset($params['status']) ? sanitize_key($params['status']) : 'publish';
    $meta_in    = isset($params['meta']) && is_array($params['meta']) ? $params['meta'] : [];
    // Acepta tanto 'taxonomy_slugs' (preferido) como el legacy 'taxonomies'.
    $tax_in     = $params['taxonomy_slugs'] ?? $params['taxonomies'] ?? [];
    $tax_in     = is_array($tax_in) ? $tax_in : [];
    $lang       = isset($params['language']) ? sanitize_key($params['language']) : 'es';

    if ($dedupe_key === '' || $title === '') {
        return new WP_Error('invalid_payload', 'Se requieren dedupe_key y title', ['status' => 400]);
    }

    $allowed_meta = [
        '_job_location', '_application', '_company_name', '_company_website',
        '_company_tagline', '_company_twitter', '_company_video', '_company_logo',
        '_job_expires', '_remote_position', '_featured', '_filled',
        '_job_salary', '_job_salary_currency', '_job_salary_unit', '_job_cover_image',
        '_jobson_source_url', '_jobson_source_type', '_jobson_test_batch',
    ];

    $existing = jobson_find_by_dedupe($dedupe_key);

    // Resolución de author: si el payload trae company_name, busca/crea employer.
    $company_name = isset($params['company_name']) ? trim((string) $params['company_name']) : '';
    if ($company_name === '' && isset($meta_in['_company_name'])) {
        $company_name = trim((string) $meta_in['_company_name']);
    }
    $employer_id = $company_name ? jobson_find_or_create_employer($company_name) : 0;

    $postarr = [
        'post_type'    => 'job_listing',
        'post_status'  => $status,
        'post_title'   => $title,
        'post_content' => $content,
    ];
    if ($employer_id) {
        $postarr['post_author'] = $employer_id;
    }
    if ($existing) {
        $postarr['ID'] = $existing;
        $post_id = wp_update_post($postarr, true);
        $action  = 'updated';
    } else {
        $post_id = wp_insert_post($postarr, true);
        $action  = 'created';
    }

    if (is_wp_error($post_id)) {
        return $post_id;
    }

    update_post_meta($post_id, '_jobson_dedupe_key', $dedupe_key);

    foreach ($meta_in as $key => $value) {
        if (!in_array($key, $allowed_meta, true)) {
            continue;
        }
        if (is_bool($value)) {
            update_post_meta($post_id, $key, $value ? 1 : 0);
        } else {
            update_post_meta($post_id, $key, is_scalar($value) ? sanitize_text_field((string) $value) : '');
        }
    }

    // Asignación de taxonomías SIN crear términos nuevos.
    // Tax map: alias REST → taxonomía real
    $tax_map = [
        'job-categories' => 'job_listing_category',
        'job-types'      => 'job_listing_type',
        'job-tag'        => 'job_listing_tag',
    ];

    $unmatched = [];

    foreach ($tax_in as $alias => $terms) {
        if (!isset($tax_map[$alias])) continue;
        $tax = $tax_map[$alias];
        if (!taxonomy_exists($tax) || !is_array($terms)) continue;

        $term_ids = [];
        foreach ($terms as $term) {
            $term = is_scalar($term) ? trim((string) $term) : '';
            if ($term === '') continue;

            $tid = jobson_resolve_term($term, $tax);
            if ($tid) {
                $term_ids[] = $tid;
            } else {
                $unmatched[] = "$alias:$term";
            }
        }

        // Fallback solo para categorías: si nada resolvió, asigna "Otros / Categoría General"
        if (!$term_ids && $alias === 'job-categories') {
            $fallback = jobson_category_fallback_id($lang);
            if ($fallback) $term_ids[] = $fallback;
        }

        if ($term_ids) {
            wp_set_object_terms($post_id, array_values(array_unique($term_ids)), $tax, false);
        }
    }

    if ($unmatched) {
        update_post_meta($post_id, '_jobson_unmatched_terms', implode('|', $unmatched));
    } else {
        delete_post_meta($post_id, '_jobson_unmatched_terms');
    }

    return rest_ensure_response([
        'action'      => $action,
        'id'          => $post_id,
        'link'        => get_permalink($post_id),
        'edit'        => get_edit_post_link($post_id, 'raw'),
        'status'      => get_post_status($post_id),
        'unmatched'   => $unmatched,
        'employer_id' => $employer_id ?: null,
    ]);
}
