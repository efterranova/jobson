<?php
/**
 * Plugin Name: Evaluador CV Connector
 * Description: Recibe registros desde el Evaluador de CV (Next.js en Vercel, https://cv-erecruit.automa.lat) y crea usuarios en WordPress de forma idempotente. Expone POST /wp-json/evaluador-cv/v1/register, protegido por un secreto compartido (constante EVALUADOR_CV_SECRET en wp-config.php).
 * Version:     1.0.0
 * Author:      Erick Terranova
 * Requires at least: 5.6
 *
 * Deploy: subir a wp-content/plugins/ y activar (o a wp-content/mu-plugins/ para autocarga).
 * Requisito: definir en wp-config.php  ->  define('EVALUADOR_CV_SECRET', '<secreto>');
 */

if (!defined('ABSPATH')) {
    exit;
}

if (!defined('EVALUADOR_CV_RATE_LIMIT')) {
    define('EVALUADOR_CV_RATE_LIMIT', 10);   // máx. peticiones por ventana
}
if (!defined('EVALUADOR_CV_RATE_WINDOW')) {
    define('EVALUADOR_CV_RATE_WINDOW', 60);  // ventana en segundos (10/min)
}

add_action('rest_api_init', function () {
    register_rest_route('evaluador-cv/v1', '/register', [
        'methods'  => 'POST',
        'callback' => 'evaluador_cv_register',
        // NOTA: la validación del secreto (hash_equals, tiempo constante) se hace
        // al inicio del handler, NO en este permission_callback. Razón: WP serializa
        // los WP_Error del permission_callback a su propio formato
        // ({"code","message","data"}) y el cliente Vercel espera EXACTAMENTE
        // {"status":"error","message":"invalid secret"}. Devolviendo WP_REST_Response
        // desde el handler controlamos el body y el status code al 100%.
        'permission_callback' => '__return_true',
    ]);
});

/**
 * IP del cliente, considerando proxies/CDN (Cloudflare, SiteGround).
 * Para rate-limit; X-Forwarded-For es spoofeable pero suficiente aquí.
 */
function evaluador_cv_client_ip(): string {
    foreach (['HTTP_CF_CONNECTING_IP', 'HTTP_X_FORWARDED_FOR', 'REMOTE_ADDR'] as $k) {
        if (!empty($_SERVER[$k])) {
            $ip = trim(explode(',', (string) $_SERVER[$k])[0]);
            if (filter_var($ip, FILTER_VALIDATE_IP)) {
                return $ip;
            }
        }
    }
    return '0.0.0.0';
}

/**
 * Deriva un username único a partir del email (parte local). Añade sufijo si
 * el username ya está ocupado.
 */
function evaluador_cv_unique_username(string $email): string {
    $base = sanitize_user(current(explode('@', $email)), true);
    if ($base === '') {
        $base = 'cv_user';
    }
    $username = $base;
    $i = 1;
    while (username_exists($username)) {
        $i++;
        $username = $base . '_' . $i;
        if ($i > 100) { // salvaguarda anti-loop
            $username = $base . '_' . wp_generate_password(6, false, false);
            break;
        }
    }
    return $username;
}

/**
 * POST /wp-json/evaluador-cv/v1/register
 */
function evaluador_cv_register(WP_REST_Request $request) {
    // 1) Constante de secreto configurada
    if (!defined('EVALUADOR_CV_SECRET') || EVALUADOR_CV_SECRET === '') {
        return new WP_REST_Response(['status' => 'error', 'message' => 'service unavailable'], 503);
    }

    // 2) Secreto válido (comparación en tiempo constante)
    $provided = (string) $request->get_header('x-evaluador-secret');
    if (!hash_equals((string) EVALUADOR_CV_SECRET, $provided)) {
        return new WP_REST_Response(['status' => 'error', 'message' => 'invalid secret'], 401);
    }

    // 3) Rate limit por IP (10/min) con transients
    $ip   = evaluador_cv_client_ip();
    $key  = 'evcv_rl_' . md5($ip);
    $hits = (int) get_transient($key);
    if ($hits >= EVALUADOR_CV_RATE_LIMIT) {
        return new WP_REST_Response(['status' => 'error', 'message' => 'rate limit exceeded'], 429);
    }
    set_transient($key, $hits + 1, EVALUADOR_CV_RATE_WINDOW);

    // 4) Parseo del body + validación de email
    $params = $request->get_json_params();
    if (!is_array($params)) {
        $params = $request->get_params();
    }
    $email = isset($params['email']) ? sanitize_email((string) $params['email']) : '';
    if (!$email || !is_email($email)) {
        return new WP_REST_Response(['status' => 'error', 'message' => 'invalid email'], 400);
    }

    // 5) Idempotencia: si el email ya existe, NO recreamos ni reenviamos correo.
    $existing = email_exists($email);
    if ($existing) {
        return new WP_REST_Response(['status' => 'exists', 'user_id' => (int) $existing], 200);
    }

    // 6) Sanitización del resto de campos
    $nombre        = isset($params['nombre'])        ? sanitize_text_field((string) $params['nombre'])        : '';
    $rol_detectado = isset($params['rol_detectado']) ? sanitize_text_field((string) $params['rol_detectado']) : '';
    $origen        = isset($params['origen'])        ? sanitize_text_field((string) $params['origen'])        : '';
    $puntaje       = isset($params['puntaje'])       ? (int) $params['puntaje']                                : 0;
    $puntaje       = max(0, min(100, $puntaje)); // acotado 0–100

    // 7) Rol destino: candidate (WP Job Manager / Cariera) si existe; si no, subscriber.
    $role = get_role('candidate') ? 'candidate' : 'subscriber';

    // 8) Crear usuario
    $username = evaluador_cv_unique_username($email);
    $user_id  = wp_insert_user([
        'user_login'   => $username,
        'user_email'   => $email,
        'user_pass'    => wp_generate_password(24),
        'display_name' => $nombre !== '' ? $nombre : $username,
        'first_name'   => $nombre,
        'role'         => $role,
    ]);

    if (is_wp_error($user_id)) {
        // Carrera: el email pudo crearse entre el check y el insert → respuesta idempotente
        if ($user_id->get_error_code() === 'existing_user_email') {
            $again = email_exists($email);
            if ($again) {
                return new WP_REST_Response(['status' => 'exists', 'user_id' => (int) $again], 200);
            }
        }
        return new WP_REST_Response(['status' => 'error', 'message' => $user_id->get_error_message()], 500);
    }

    // 9) Meta del evaluador
    update_user_meta($user_id, 'cv_puntaje',        $puntaje);
    update_user_meta($user_id, 'cv_rol_detectado',  $rol_detectado);
    update_user_meta($user_id, 'cv_origen',         $origen);
    update_user_meta($user_id, 'cv_fecha_registro', current_time('mysql'));

    // 10) Email estándar de WP para que el usuario configure su contraseña.
    //     Solo al CREAR (idempotencia: nunca se reenvía a emails ya existentes).
    if (!function_exists('wp_new_user_notification')) {
        require_once ABSPATH . WPINC . '/pluggable.php';
    }
    wp_new_user_notification($user_id, null, 'user');

    return new WP_REST_Response(['status' => 'created', 'user_id' => (int) $user_id], 200);
}

/**
 * Aviso en el admin si falta el secreto (el endpoint responde 503 hasta definirlo).
 */
add_action('admin_notices', function () {
    if (!defined('EVALUADOR_CV_SECRET') || EVALUADOR_CV_SECRET === '') {
        echo '<div class="notice notice-error"><p><strong>Evaluador CV Connector:</strong> falta definir <code>EVALUADOR_CV_SECRET</code> en <code>wp-config.php</code>. El endpoint <code>/wp-json/evaluador-cv/v1/register</code> responderá 503 hasta entonces.</p></div>';
    }
});
