<?php
/**
 * Copia este archivo a config.php y completa valores reales.
 * Ubicación recomendada en Hostinger:
 * /home/TU_USUARIO/private/config.php (fuera de public_html)
 */
return [
    'SUPABASE_URL' => 'https://TU-PROYECTO.supabase.co',
    'SUPABASE_SERVICE_KEY' => 'TU_SERVICE_ROLE_KEY',
    'SUPABASE_TABLE' => 'linkedin_results',

    // Nuevo módulo Perfil IA (cloud)
    'PROFILE_TABLE' => 'career_profiles',
    'PROFILE_REPORTS_TABLE' => 'career_profile_reports',
    // true = mantiene un perfil principal y lo actualiza en cada guardado.
    'PROFILE_SINGLETON' => true,

    // Opcional pero recomendado para guardar los archivos CV en Supabase Storage.
    // Crea bucket en Supabase con este nombre.
    'SUPABASE_STORAGE_BUCKET' => 'jobson-cv',
    'MAX_CV_FILE_MB' => 6,

    // OpenRouter para generar brief + análisis IA.
    'OPENROUTER_API_KEY' => 'TU_OPENROUTER_API_KEY',
    'OPENROUTER_MODEL' => 'openai/gpt-4o-mini',
    'OPENROUTER_BASE_URL' => 'https://openrouter.ai/api/v1',
    'OPENROUTER_APP_URL' => 'https://TU-DOMINIO.com',
    'OPENROUTER_APP_NAME' => 'JobsOn',
];
