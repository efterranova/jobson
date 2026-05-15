<?php
declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');

$candidateConfigPaths = [];

$forcedPath = getenv('JOBSON_CONFIG_PATH');
if (is_string($forcedPath) && trim($forcedPath) !== '') {
    $candidateConfigPaths[] = trim($forcedPath);
}

// Opcion recomendada: fuera de public_html.
$candidateConfigPaths[] = dirname(__DIR__) . '/private/config.php';
$candidateConfigPaths[] = __DIR__ . '/../private/config.php';

// Rutas comunes en shared hosting.
if (isset($_SERVER['DOCUMENT_ROOT'])) {
    $candidateConfigPaths[] = rtrim(dirname((string)$_SERVER['DOCUMENT_ROOT']), '/\\') . '/private/config.php';
}
if (isset($_SERVER['HOME'])) {
    $candidateConfigPaths[] = rtrim((string)$_SERVER['HOME'], '/\\') . '/private/config.php';
}

// Fallback por compatibilidad en hostings con restricciones open_basedir.
$candidateConfigPaths[] = __DIR__ . '/config.php';

$configPath = null;
$checkedPaths = [];
foreach (array_unique(array_filter($candidateConfigPaths)) as $path) {
    $exists = file_exists($path);
    $readable = is_readable($path);
    $checkedPaths[] = [
        'path' => $path,
        'exists' => $exists,
        'readable' => $readable,
    ];
    if ($exists && $readable) {
        $configPath = $path;
        break;
    }
}

if ($configPath === null) {
    http_response_code(500);
    echo json_encode([
        'error' => 'No se encontró config.php en rutas esperadas.',
        'hint' => 'Ubica config.php en /private/config.php (recomendado) o en public_html/config.php (fallback).',
        'checked_paths' => $checkedPaths,
    ]);
    exit;
}

$config = require $configPath;
if (!is_array($config)) {
    http_response_code(500);
    echo json_encode(['error' => 'config.php debe retornar un array.']);
    exit;
}

$SUPABASE_URL = firstNonEmptyString([
    $config['SUPABASE_URL'] ?? null,
    $config['SUPABASE_PROJECT_URL'] ?? null,
    getenv('SUPABASE_URL') ?: null,
]);
$SUPABASE_URL = preg_replace('/\s+/', '', (string)$SUPABASE_URL);
$SUPABASE_URL = rtrim((string)$SUPABASE_URL, '/');

$SUPABASE_KEY = firstNonEmptyString([
    $config['SUPABASE_SERVICE_KEY'] ?? null,
    $config['SUPABASE_KEY'] ?? null,
    $config['SUPABASE_SERVICE_ROLE_KEY'] ?? null,
    getenv('SUPABASE_SERVICE_KEY') ?: null,
    getenv('SUPABASE_KEY') ?: null,
    getenv('SUPABASE_SERVICE_ROLE_KEY') ?: null,
]);

$SUPABASE_TABLE = trim((string)($config['SUPABASE_TABLE'] ?? 'linkedin_results')) ?: 'linkedin_results';
$PROFILE_TABLE = trim((string)($config['PROFILE_TABLE'] ?? 'career_profiles')) ?: 'career_profiles';
$PROFILE_REPORTS_TABLE = trim((string)($config['PROFILE_REPORTS_TABLE'] ?? 'career_profile_reports')) ?: 'career_profile_reports';

$OPENROUTER_API_KEY = firstNonEmptyString([
    $config['OPENROUTER_API_KEY'] ?? null,
    getenv('OPENROUTER_API_KEY') ?: null,
]);
$OPENROUTER_MODEL = trim((string)($config['OPENROUTER_MODEL'] ?? 'openai/gpt-4o-mini')) ?: 'openai/gpt-4o-mini';
$OPENROUTER_BASE_URL = rtrim(trim((string)($config['OPENROUTER_BASE_URL'] ?? 'https://openrouter.ai/api/v1')), '/');
$OPENROUTER_APP_URL = trim((string)($config['OPENROUTER_APP_URL'] ?? ''));
$OPENROUTER_APP_NAME = trim((string)($config['OPENROUTER_APP_NAME'] ?? 'JobsOn')) ?: 'JobsOn';

$SUPABASE_STORAGE_BUCKET = trim((string)($config['SUPABASE_STORAGE_BUCKET'] ?? ''));
$MAX_CV_FILE_MB = max(1, min(20, (int)($config['MAX_CV_FILE_MB'] ?? 6)));
$MAX_CV_FILE_BYTES = $MAX_CV_FILE_MB * 1024 * 1024;
$PROFILE_SINGLETON = toBool($config['PROFILE_SINGLETON'] ?? true);

if ($SUPABASE_URL === '' || $SUPABASE_KEY === '') {
    http_response_code(500);
    echo json_encode([
        'error' => 'Config incompleta: SUPABASE_URL o SUPABASE key vacíos.',
        'config_path' => $configPath,
        'config_keys_detected' => array_keys($config),
        'url_detected' => $SUPABASE_URL !== '',
        'key_detected' => $SUPABASE_KEY !== '',
        'hint' => 'Usa SUPABASE_URL y SUPABASE_SERVICE_KEY (o SUPABASE_KEY) dentro de config.php.',
    ]);
    exit;
}

$allowedStatus = ['me_interesa', 'no_me_interesa', 'ya_aplique'];
$allowedStatusFilters = ['all', 'seguimiento', 'me_interesa', 'no_me_interesa', 'ya_aplique'];
$allowedVisibility = ['active', 'deleted', 'all'];

$action = (string)($_GET['action'] ?? 'results');
$method = $_SERVER['REQUEST_METHOD'] ?? 'GET';

if ($action === 'results' && $method === 'GET') {
    $mode = strtolower(trim((string)($_GET['mode'] ?? 'all')));
    $query = trim((string)($_GET['q'] ?? ''));
    $statusFilter = strtolower(trim((string)($_GET['status'] ?? 'all')));
    $keywordFilter = trim((string)($_GET['keyword'] ?? 'all'));
    $visibility = strtolower(trim((string)($_GET['visibility'] ?? 'active')));
    $limitRaw = (string)($_GET['limit'] ?? '200');
    $limit = max(1, min(1000, (int)$limitRaw));

    if (!in_array($statusFilter, $allowedStatusFilters, true)) {
        respond(400, ['error' => 'status inválido']);
    }
    if (!in_array($visibility, $allowedVisibility, true)) {
        respond(400, ['error' => 'visibility inválido']);
    }

    $params = [
        'select' => '*',
        'order' => 'scraped_at.desc',
        'limit' => (string)$limit,
    ];

    if ($mode === 'jobs' || $mode === 'feed') {
        $params['source_type'] = 'eq.' . $mode;
    }

    if ($keywordFilter !== '' && strtolower($keywordFilter) !== 'all') {
        $params['keyword'] = 'eq.' . $keywordFilter;
    }

    if ($visibility === 'active') {
        $params['deleted_at'] = 'is.null';
    } elseif ($visibility === 'deleted') {
        $params['deleted_at'] = 'not.is.null';
    }

    if ($statusFilter === 'seguimiento') {
        $params['user_status'] = 'not.is.null';
    } elseif (in_array($statusFilter, $allowedStatus, true)) {
        $params['user_status'] = 'eq.' . $statusFilter;
    }

    if ($query !== '') {
        $q = str_replace('%', '', $query);
        $params['or'] = sprintf(
            '(title.ilike.*%1$s*,company.ilike.*%1$s*,author.ilike.*%1$s*,summary.ilike.*%1$s*,content.ilike.*%1$s*)',
            $q
        );
    }

    [$ok, $payload] = supabaseRequest('GET', $SUPABASE_URL, $SUPABASE_TABLE, $SUPABASE_KEY, $params, null);
    if (!$ok) {
        respond(502, [
            'error' => 'No se pudo leer Supabase.',
            'detail' => $payload,
        ]);
    }

    $rows = payloadRows($payload);
    $summary = [
        'total' => count($rows),
        'jobs' => count(array_filter($rows, fn($row) => ($row['source_type'] ?? '') === 'jobs')),
        'feed' => count(array_filter($rows, fn($row) => ($row['source_type'] ?? '') === 'feed')),
        'me_interesa' => count(array_filter($rows, fn($row) => ($row['user_status'] ?? '') === 'me_interesa')),
        'no_me_interesa' => count(array_filter($rows, fn($row) => ($row['user_status'] ?? '') === 'no_me_interesa')),
        'ya_aplique' => count(array_filter($rows, fn($row) => ($row['user_status'] ?? '') === 'ya_aplique')),
    ];

    respond(200, ['records' => $rows, 'summary' => $summary]);
}

if ($action === 'keywords' && $method === 'GET') {
    $params = [
        'select' => 'keyword,scraped_at',
        'order' => 'scraped_at.desc',
        'limit' => '3000',
        'deleted_at' => 'is.null',
    ];

    [$ok, $payload] = supabaseRequest('GET', $SUPABASE_URL, $SUPABASE_TABLE, $SUPABASE_KEY, $params, null);
    if (!$ok) {
        respond(502, [
            'error' => 'No se pudo leer keywords desde Supabase.',
            'detail' => $payload,
        ]);
    }

    $rows = payloadRows($payload);
    $seen = [];
    $keywords = [];
    foreach ($rows as $row) {
        $keyword = trim((string)($row['keyword'] ?? ''));
        if ($keyword === '') {
            continue;
        }
        if (isset($seen[$keyword])) {
            continue;
        }
        $seen[$keyword] = true;
        $keywords[] = $keyword;
    }

    respond(200, ['keywords' => $keywords]);
}

if ($action === 'delete' && $method === 'POST') {
    $body = parseJsonBody();

    $dedupeKey = trim((string)($body['dedupe_key'] ?? ''));
    if ($dedupeKey === '') {
        respond(400, ['error' => 'dedupe_key es obligatorio']);
    }

    $patchPayload = [
        'deleted_at' => gmdate('c'),
    ];

    $params = [
        'dedupe_key' => 'eq.' . $dedupeKey,
    ];

    [$ok, $payload] = supabaseRequest(
        'PATCH',
        $SUPABASE_URL,
        $SUPABASE_TABLE,
        $SUPABASE_KEY,
        $params,
        $patchPayload,
        ['Prefer: return=representation']
    );

    if (!$ok) {
        respond(502, ['error' => 'No se pudo eliminar el registro.', 'detail' => $payload]);
    }

    $rows = payloadRows($payload);
    if (count($rows) === 0) {
        respond(404, ['error' => 'Registro no encontrado']);
    }

    respond(200, ['record' => $rows[0]]);
}

if ($action === 'status' && $method === 'POST') {
    $body = parseJsonBody();

    $dedupeKey = trim((string)($body['dedupe_key'] ?? ''));
    $userStatus = $body['user_status'] ?? null;
    $userStatus = is_string($userStatus) ? strtolower(trim($userStatus)) : null;

    if ($dedupeKey === '') {
        respond(400, ['error' => 'dedupe_key es obligatorio']);
    }

    if ($userStatus !== null && !in_array($userStatus, $allowedStatus, true)) {
        respond(400, ['error' => 'user_status inválido']);
    }

    $patchPayload = [
        'user_status' => $userStatus,
        'status_updated_at' => gmdate('c'),
    ];

    $params = [
        'dedupe_key' => 'eq.' . $dedupeKey,
    ];

    [$ok, $payload] = supabaseRequest(
        'PATCH',
        $SUPABASE_URL,
        $SUPABASE_TABLE,
        $SUPABASE_KEY,
        $params,
        $patchPayload,
        ['Prefer: return=representation']
    );

    if (!$ok) {
        respond(502, ['error' => 'No se pudo actualizar estado.', 'detail' => $payload]);
    }

    $rows = payloadRows($payload);
    if (count($rows) === 0) {
        respond(404, ['error' => 'Registro no encontrado']);
    }

    respond(200, ['record' => $rows[0]]);
}

if ($action === 'profiles' && $method === 'GET') {
    $limitRaw = (string)($_GET['limit'] ?? '50');
    $limit = max(1, min(200, (int)$limitRaw));

    [$ok, $payload] = supabaseRequest(
        'GET',
        $SUPABASE_URL,
        $PROFILE_TABLE,
        $SUPABASE_KEY,
        [
            'select' => 'id,full_name,headline,target_roles,preferred_languages,updated_at,ai_generated_at,ai_model,ai_last_error,cv_es_filename,cv_en_filename',
            'order' => 'updated_at.desc',
            'limit' => (string)$limit,
        ],
        null
    );

    if (!$ok) {
        respond(502, ['error' => 'No se pudo leer perfiles.', 'detail' => $payload]);
    }

    respond(200, ['profiles' => payloadRows($payload)]);
}

if ($action === 'profile_get' && $method === 'GET') {
    $profileId = trim((string)($_GET['profile_id'] ?? ''));

    $params = [
        'select' => '*',
        'order' => 'updated_at.desc',
        'limit' => '1',
    ];
    if ($profileId !== '') {
        $params = [
            'select' => '*',
            'id' => 'eq.' . $profileId,
            'limit' => '1',
        ];
    }

    [$ok, $payload] = supabaseRequest('GET', $SUPABASE_URL, $PROFILE_TABLE, $SUPABASE_KEY, $params, null);
    if (!$ok) {
        respond(502, ['error' => 'No se pudo leer perfil.', 'detail' => $payload]);
    }

    $rows = payloadRows($payload);
    if (count($rows) === 0) {
        respond(200, ['profile' => null, 'latest_report' => null, 'reports' => []]);
    }

    $profile = $rows[0];
    $profileId = (string)($profile['id'] ?? '');

    $latestReport = null;
    if ($profileId !== '') {
        [$reportsOk, $reportsPayload] = supabaseRequest(
            'GET',
            $SUPABASE_URL,
            $PROFILE_REPORTS_TABLE,
            $SUPABASE_KEY,
            [
                'select' => '*',
                'profile_id' => 'eq.' . $profileId,
                'order' => 'created_at.desc',
                'limit' => '1',
            ],
            null
        );
        if ($reportsOk) {
            $reports = payloadRows($reportsPayload);
            $latestReport = $reports[0] ?? null;
        }
    }

    respond(200, ['profile' => $profile, 'latest_report' => $latestReport]);
}

if ($action === 'profile_reports' && $method === 'GET') {
    $profileId = trim((string)($_GET['profile_id'] ?? ''));
    if ($profileId === '') {
        respond(400, ['error' => 'profile_id es obligatorio']);
    }

    $limitRaw = (string)($_GET['limit'] ?? '20');
    $limit = max(1, min(100, (int)$limitRaw));

    [$ok, $payload] = supabaseRequest(
        'GET',
        $SUPABASE_URL,
        $PROFILE_REPORTS_TABLE,
        $SUPABASE_KEY,
        [
            'select' => '*',
            'profile_id' => 'eq.' . $profileId,
            'order' => 'created_at.desc',
            'limit' => (string)$limit,
        ],
        null
    );

    if (!$ok) {
        respond(502, ['error' => 'No se pudo leer reportes de IA.', 'detail' => $payload]);
    }

    respond(200, ['reports' => payloadRows($payload)]);
}

if ($action === 'profile_save' && $method === 'POST') {
    $isMultipart = isMultipartRequest();
    $input = $isMultipart ? $_POST : parseJsonBody();
    if (!is_array($input)) {
        respond(400, ['error' => 'Payload inválido']);
    }

    $runAI = toBool($input['run_ai'] ?? true);
    $profileId = normalizeShortText($input['profile_id'] ?? '', 80);

    $profileRow = [
        'full_name' => normalizeShortText($input['full_name'] ?? '', 180),
        'headline' => normalizeShortText($input['headline'] ?? '', 240),
        'email' => normalizeShortText($input['email'] ?? '', 180),
        'phone' => normalizeShortText($input['phone'] ?? '', 80),
        'location' => normalizeShortText($input['location'] ?? '', 180),
        'linkedin_url' => normalizeShortText($input['linkedin_url'] ?? '', 350),
        'portfolio_url' => normalizeShortText($input['portfolio_url'] ?? '', 350),
        'work_authorization' => normalizeShortText($input['work_authorization'] ?? '', 200),
        'target_roles' => normalizeLongText($input['target_roles'] ?? '', 4000),
        'target_industries' => normalizeLongText($input['target_industries'] ?? '', 3000),
        'target_countries' => normalizeLongText($input['target_countries'] ?? '', 3000),
        'work_modes' => normalizeLongText($input['work_modes'] ?? '', 1200),
        'employment_types' => normalizeLongText($input['employment_types'] ?? '', 1200),
        'seniority_target' => normalizeShortText($input['seniority_target'] ?? '', 160),
        'salary_expectation' => normalizeShortText($input['salary_expectation'] ?? '', 180),
        'preferred_languages' => normalizeLongText($input['preferred_languages'] ?? '', 1200),
        'skills_core' => normalizeLongText($input['skills_core'] ?? '', 7000),
        'strengths' => normalizeLongText($input['strengths'] ?? '', 7000),
        'constraints' => normalizeLongText($input['constraints'] ?? '', 7000),
        'notes' => normalizeLongText($input['notes'] ?? '', 7000),
    ];

    if ($profileRow['full_name'] === '') {
        respond(400, ['error' => 'full_name es obligatorio']);
    }
    if ($profileRow['target_roles'] === '') {
        respond(400, ['error' => 'target_roles es obligatorio']);
    }

    $existingProfile = null;
    if ($profileId !== '') {
        [$existingOk, $existingPayload] = supabaseRequest(
            'GET',
            $SUPABASE_URL,
            $PROFILE_TABLE,
            $SUPABASE_KEY,
            ['select' => '*', 'id' => 'eq.' . $profileId, 'limit' => '1'],
            null
        );
        if (!$existingOk) {
            respond(502, ['error' => 'No se pudo consultar perfil existente.', 'detail' => $existingPayload]);
        }
        $existingRows = payloadRows($existingPayload);
        if (count($existingRows) === 0) {
            respond(404, ['error' => 'No existe el perfil indicado para actualizar.']);
        }
        $existingProfile = $existingRows[0];
    } elseif ($PROFILE_SINGLETON) {
        // Default behavior for single-user setups:
        // if no profile_id arrives, update latest profile instead of creating duplicates.
        [$latestOk, $latestPayload] = supabaseRequest(
            'GET',
            $SUPABASE_URL,
            $PROFILE_TABLE,
            $SUPABASE_KEY,
            ['select' => '*', 'order' => 'updated_at.desc', 'limit' => '1'],
            null
        );
        if (!$latestOk) {
            respond(502, ['error' => 'No se pudo consultar perfil actual.', 'detail' => $latestPayload]);
        }
        $latestRows = payloadRows($latestPayload);
        if (count($latestRows) > 0 && isset($latestRows[0]['id'])) {
            $existingProfile = $latestRows[0];
            $profileId = (string)$latestRows[0]['id'];
        }
    }

    $cvEsUpload = processCvUpload(
        'cv_es_file',
        'es',
        $MAX_CV_FILE_BYTES,
        $SUPABASE_STORAGE_BUCKET,
        $SUPABASE_URL,
        $SUPABASE_KEY
    );
    if ($cvEsUpload['error'] !== null) {
        respond(400, ['error' => 'CV español: ' . $cvEsUpload['error']]);
    }

    $cvEnUpload = processCvUpload(
        'cv_en_file',
        'en',
        $MAX_CV_FILE_BYTES,
        $SUPABASE_STORAGE_BUCKET,
        $SUPABASE_URL,
        $SUPABASE_KEY
    );
    if ($cvEnUpload['error'] !== null) {
        respond(400, ['error' => 'CV inglés: ' . $cvEnUpload['error']]);
    }

    $manualCvEs = normalizeLongText($input['cv_es_text'] ?? '', 50000);
    $manualCvEn = normalizeLongText($input['cv_en_text'] ?? '', 50000);

    $profileRow['cv_es_filename'] = $cvEsUpload['filename']
        ?? valueOrNull($existingProfile['cv_es_filename'] ?? null);
    $profileRow['cv_es_storage_path'] = $cvEsUpload['storage_path']
        ?? valueOrNull($existingProfile['cv_es_storage_path'] ?? null);
    $profileRow['cv_es_text'] = mergeCvText(
        $manualCvEs,
        $cvEsUpload['text'] ?? '',
        (string)($existingProfile['cv_es_text'] ?? '')
    );

    $profileRow['cv_en_filename'] = $cvEnUpload['filename']
        ?? valueOrNull($existingProfile['cv_en_filename'] ?? null);
    $profileRow['cv_en_storage_path'] = $cvEnUpload['storage_path']
        ?? valueOrNull($existingProfile['cv_en_storage_path'] ?? null);
    $profileRow['cv_en_text'] = mergeCvText(
        $manualCvEn,
        $cvEnUpload['text'] ?? '',
        (string)($existingProfile['cv_en_text'] ?? '')
    );

    $nowIso = gmdate('c');
    $profileRow['updated_at'] = $nowIso;

    $profile = null;
    if ($profileId !== '') {
        [$ok, $payload] = supabaseRequest(
            'PATCH',
            $SUPABASE_URL,
            $PROFILE_TABLE,
            $SUPABASE_KEY,
            ['id' => 'eq.' . $profileId],
            $profileRow,
            ['Prefer: return=representation']
        );
        if (!$ok) {
            respond(502, ['error' => 'No se pudo actualizar el perfil.', 'detail' => $payload]);
        }
        $rows = payloadRows($payload);
        $profile = $rows[0] ?? null;
    } else {
        $profileRow['created_at'] = $nowIso;
        [$ok, $payload] = supabaseRequest(
            'POST',
            $SUPABASE_URL,
            $PROFILE_TABLE,
            $SUPABASE_KEY,
            [],
            [$profileRow],
            ['Prefer: return=representation']
        );
        if (!$ok) {
            respond(502, ['error' => 'No se pudo crear el perfil.', 'detail' => $payload]);
        }
        $rows = payloadRows($payload);
        $profile = $rows[0] ?? null;
    }

    if (!is_array($profile) || !isset($profile['id'])) {
        respond(500, ['error' => 'No se recibió el perfil persistido desde Supabase.']);
    }

    $profileId = (string)$profile['id'];
    $analysisError = null;
    $latestReport = null;

    if ($runAI) {
        if ($OPENROUTER_API_KEY === '') {
            $analysisError = 'OPENROUTER_API_KEY no está configurada en config.php.';
            supabaseRequest(
                'PATCH',
                $SUPABASE_URL,
                $PROFILE_TABLE,
                $SUPABASE_KEY,
                ['id' => 'eq.' . $profileId],
                ['ai_last_error' => $analysisError, 'updated_at' => gmdate('c')],
                []
            );
        } else {
            $analysisInput = buildAnalysisInput($profile);
            $analysisResult = openrouterAnalyzeProfile(
                $OPENROUTER_API_KEY,
                $OPENROUTER_MODEL,
                $OPENROUTER_BASE_URL,
                $analysisInput,
                $OPENROUTER_APP_URL,
                $OPENROUTER_APP_NAME
            );

            if (!($analysisResult['ok'] ?? false)) {
                $analysisError = (string)($analysisResult['error'] ?? 'No se pudo generar análisis IA.');
                supabaseRequest(
                    'PATCH',
                    $SUPABASE_URL,
                    $PROFILE_TABLE,
                    $SUPABASE_KEY,
                    ['id' => 'eq.' . $profileId],
                    ['ai_last_error' => $analysisError, 'updated_at' => gmdate('c')],
                    []
                );
            } else {
                $brief = normalizeLongText($analysisResult['brief'] ?? '', 6000);
                $analysisText = normalizeLongText($analysisResult['analysis'] ?? '', 32000);
                $structured = is_array($analysisResult['structured'] ?? null) ? $analysisResult['structured'] : [];

                if ($brief === '' && $analysisText !== '') {
                    $brief = safeSubstr($analysisText, 0, 600);
                }

                $reportRow = [
                    'profile_id' => $profileId,
                    'provider' => 'openrouter',
                    'model' => $OPENROUTER_MODEL,
                    'brief' => $brief,
                    'analysis' => $analysisText,
                    'structured' => $structured,
                    'input_snapshot' => $analysisInput,
                    'prompt_version' => 'v1-profile-brief',
                    'created_at' => gmdate('c'),
                ];

                [$reportOk, $reportPayload] = supabaseRequest(
                    'POST',
                    $SUPABASE_URL,
                    $PROFILE_REPORTS_TABLE,
                    $SUPABASE_KEY,
                    [],
                    [$reportRow],
                    ['Prefer: return=representation']
                );

                if (!$reportOk) {
                    $analysisError = 'Se generó análisis IA, pero no se pudo guardar reporte: ' . json_encode($reportPayload);
                } else {
                    $reportRows = payloadRows($reportPayload);
                    $latestReport = $reportRows[0] ?? null;
                }

                supabaseRequest(
                    'PATCH',
                    $SUPABASE_URL,
                    $PROFILE_TABLE,
                    $SUPABASE_KEY,
                    ['id' => 'eq.' . $profileId],
                    [
                        'ai_brief' => $brief,
                        'ai_analysis' => $analysisText,
                        'ai_structured' => $structured,
                        'ai_model' => $OPENROUTER_MODEL,
                        'ai_generated_at' => gmdate('c'),
                        'ai_last_error' => $analysisError,
                        'updated_at' => gmdate('c'),
                    ],
                    []
                );
            }
        }
    }

    [$freshOk, $freshPayload] = supabaseRequest(
        'GET',
        $SUPABASE_URL,
        $PROFILE_TABLE,
        $SUPABASE_KEY,
        ['select' => '*', 'id' => 'eq.' . $profileId, 'limit' => '1'],
        null
    );
    if ($freshOk) {
        $freshRows = payloadRows($freshPayload);
        if (count($freshRows) > 0) {
            $profile = $freshRows[0];
        }
    }

    if ($latestReport === null) {
        [$reportOk, $reportPayload] = supabaseRequest(
            'GET',
            $SUPABASE_URL,
            $PROFILE_REPORTS_TABLE,
            $SUPABASE_KEY,
            ['select' => '*', 'profile_id' => 'eq.' . $profileId, 'order' => 'created_at.desc', 'limit' => '1'],
            null
        );
        if ($reportOk) {
            $reportRows = payloadRows($reportPayload);
            $latestReport = $reportRows[0] ?? null;
        }
    }

    respond(200, [
        'profile' => $profile,
        'latest_report' => $latestReport,
        'analysis_error' => $analysisError,
        'run_ai' => $runAI,
        'persisted' => true,
        'uploads' => [
            'cv_es' => [
                'filename' => $cvEsUpload['filename'] ?? null,
                'storage_path' => $cvEsUpload['storage_path'] ?? null,
                'extracted_chars' => safeLength($cvEsUpload['text'] ?? ''),
            ],
            'cv_en' => [
                'filename' => $cvEnUpload['filename'] ?? null,
                'storage_path' => $cvEnUpload['storage_path'] ?? null,
                'extracted_chars' => safeLength($cvEnUpload['text'] ?? ''),
            ],
        ],
    ]);
}

respond(404, ['error' => 'Ruta o método no soportado']);

function respond(int $status, array $data): void
{
    http_response_code($status);
    echo json_encode($data, JSON_UNESCAPED_UNICODE);
    exit;
}

function parseJsonBody(): array
{
    $raw = file_get_contents('php://input');
    $body = json_decode($raw ?: '{}', true);
    return is_array($body) ? $body : [];
}

function isMultipartRequest(): bool
{
    $contentType = strtolower((string)($_SERVER['CONTENT_TYPE'] ?? ''));
    return str_contains($contentType, 'multipart/form-data');
}

function toBool(mixed $value): bool
{
    if (is_bool($value)) {
        return $value;
    }
    if (is_int($value) || is_float($value)) {
        return ((int)$value) !== 0;
    }
    if (!is_string($value)) {
        return false;
    }
    $normalized = strtolower(trim($value));
    if ($normalized === '') {
        return false;
    }
    return in_array($normalized, ['1', 'true', 'yes', 'si', 'sí', 'on'], true);
}

/**
 * @return array{0:bool,1:mixed,2:int}
 */
function supabaseRequest(
    string $method,
    string $baseUrl,
    string $table,
    string $key,
    array $params,
    mixed $jsonBody,
    array $extraHeaders = []
): array {
    $url = $baseUrl . '/rest/v1/' . rawurlencode($table);
    if (!empty($params)) {
        $url .= '?' . http_build_query($params, '', '&', PHP_QUERY_RFC3986);
    }

    $ch = curl_init($url);
    if ($ch === false) {
        return [false, 'No se pudo inicializar cURL', 500];
    }

    $headers = [
        'apikey: ' . $key,
        'Authorization: Bearer ' . $key,
        'Accept: application/json',
        'Content-Type: application/json',
    ];
    foreach ($extraHeaders as $header) {
        $headers[] = $header;
    }

    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_CUSTOMREQUEST, $method);
    curl_setopt($ch, CURLOPT_HTTPHEADER, $headers);
    curl_setopt($ch, CURLOPT_TIMEOUT, 45);

    if ($jsonBody !== null) {
        curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($jsonBody, JSON_UNESCAPED_UNICODE));
    }

    $raw = curl_exec($ch);
    $errno = curl_errno($ch);
    $error = curl_error($ch);
    $httpCode = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);

    if ($errno !== 0) {
        return [false, 'cURL error: ' . $error, 500];
    }

    $decoded = json_decode((string)$raw, true);
    $payload = $decoded ?? (string)$raw;

    if ($httpCode >= 200 && $httpCode < 300) {
        return [true, $payload, $httpCode];
    }

    return [false, $payload, $httpCode];
}

/**
 * @return array<int,array<string,mixed>>
 */
function payloadRows(mixed $payload): array
{
    if (!is_array($payload)) {
        return [];
    }
    $isList = true;
    $index = 0;
    foreach (array_keys($payload) as $key) {
        if ($key !== $index) {
            $isList = false;
            break;
        }
        $index++;
    }
    if ($isList) {
        return $payload;
    }
    return [$payload];
}

function firstNonEmptyString(array $values): string
{
    foreach ($values as $value) {
        if (!is_string($value)) {
            continue;
        }
        $trimmed = trim($value);
        if ($trimmed !== '') {
            return $trimmed;
        }
    }
    return '';
}

function normalizeShortText(mixed $value, int $maxLen = 240): string
{
    $text = normalizeLongText($value, $maxLen);
    return $text;
}

function normalizeLongText(mixed $value, int $maxLen = 12000): string
{
    if ($value === null) {
        return '';
    }
    $text = is_string($value) ? $value : json_encode($value, JSON_UNESCAPED_UNICODE);
    if (!is_string($text)) {
        return '';
    }

    $text = str_replace(["\r\n", "\r"], "\n", $text);
    $text = preg_replace('/[\t ]+/u', ' ', $text) ?? $text;
    $text = preg_replace("/\n{3,}/u", "\n\n", $text) ?? $text;
    $text = trim($text);

    if ($maxLen > 0 && safeLength($text) > $maxLen) {
        $text = safeSubstr($text, 0, $maxLen);
    }

    return trim($text);
}

function safeSubstr(string $text, int $start, int $length): string
{
    if (function_exists('mb_substr')) {
        return (string)mb_substr($text, $start, $length, 'UTF-8');
    }
    return substr($text, $start, $length);
}

function safeLength(string $text): int
{
    if (function_exists('mb_strlen')) {
        return (int)mb_strlen($text, 'UTF-8');
    }
    return strlen($text);
}

function valueOrNull(mixed $value): ?string
{
    if (!is_string($value)) {
        return null;
    }
    $trimmed = trim($value);
    return $trimmed === '' ? null : $trimmed;
}

/**
 * @return array{filename:?string,storage_path:?string,text:string,error:?string}
 */
function processCvUpload(
    string $fieldName,
    string $langCode,
    int $maxBytes,
    string $storageBucket,
    string $supabaseUrl,
    string $supabaseKey
): array {
    $result = [
        'filename' => null,
        'storage_path' => null,
        'text' => '',
        'error' => null,
    ];

    if (!isset($_FILES[$fieldName]) || !is_array($_FILES[$fieldName])) {
        return $result;
    }

    $file = $_FILES[$fieldName];
    $errorCode = (int)($file['error'] ?? UPLOAD_ERR_NO_FILE);
    if ($errorCode === UPLOAD_ERR_NO_FILE) {
        return $result;
    }

    if ($errorCode !== UPLOAD_ERR_OK) {
        $result['error'] = uploadErrorMessage($errorCode);
        return $result;
    }

    $tmpName = (string)($file['tmp_name'] ?? '');
    $originalName = sanitizeFileName((string)($file['name'] ?? 'cv_' . $langCode));
    $size = (int)($file['size'] ?? 0);

    if ($tmpName === '' || !file_exists($tmpName)) {
        $result['error'] = 'No se encontró el archivo temporal.';
        return $result;
    }

    if ($size <= 0) {
        $result['error'] = 'El archivo está vacío.';
        return $result;
    }

    if ($size > $maxBytes) {
        $result['error'] = 'El archivo supera el máximo permitido.';
        return $result;
    }

    $ext = strtolower((string)pathinfo($originalName, PATHINFO_EXTENSION));
    $allowed = ['pdf', 'docx', 'txt', 'md', 'rtf'];
    if (!in_array($ext, $allowed, true)) {
        $result['error'] = 'Formato no soportado. Usa pdf, docx, txt, md o rtf.';
        return $result;
    }

    $mime = detectMimeType($tmpName, $ext);
    $result['filename'] = $originalName;
    $result['text'] = extractTextFromCv($tmpName, $originalName, $ext);

    if ($storageBucket !== '') {
        $storagePath = 'profiles/' . gmdate('Y/m') . '/' . $langCode . '_' . bin2hex(random_bytes(8)) . '_' . $originalName;
        [$ok, $uploadPayload] = supabaseStorageUpload(
            $supabaseUrl,
            $supabaseKey,
            $storageBucket,
            $storagePath,
            $tmpName,
            $mime
        );
        if (!$ok) {
            $result['error'] = 'No se pudo subir archivo a Supabase Storage: ' . json_encode($uploadPayload, JSON_UNESCAPED_UNICODE);
            return $result;
        }
        $result['storage_path'] = $storagePath;
    }

    return $result;
}

function sanitizeFileName(string $name): string
{
    $name = trim($name);
    if ($name === '') {
        return 'cv_file';
    }

    $name = preg_replace('/[^A-Za-z0-9._-]+/', '_', $name) ?? $name;
    $name = trim($name, '._-');
    if ($name === '') {
        return 'cv_file';
    }

    return safeSubstr($name, 0, 120);
}

function detectMimeType(string $tmpName, string $ext): string
{
    if (function_exists('mime_content_type')) {
        $detected = @mime_content_type($tmpName);
        if (is_string($detected) && trim($detected) !== '') {
            return trim($detected);
        }
    }

    return match ($ext) {
        'pdf' => 'application/pdf',
        'docx' => 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'md', 'txt' => 'text/plain',
        'rtf' => 'application/rtf',
        default => 'application/octet-stream',
    };
}

function uploadErrorMessage(int $errorCode): string
{
    return match ($errorCode) {
        UPLOAD_ERR_INI_SIZE, UPLOAD_ERR_FORM_SIZE => 'El archivo supera el límite de subida del servidor.',
        UPLOAD_ERR_PARTIAL => 'La subida del archivo quedó incompleta.',
        UPLOAD_ERR_NO_TMP_DIR => 'No hay carpeta temporal disponible en el servidor.',
        UPLOAD_ERR_CANT_WRITE => 'No se pudo escribir el archivo en disco.',
        UPLOAD_ERR_EXTENSION => 'Una extensión de PHP detuvo la subida.',
        default => 'Error desconocido al subir archivo.',
    };
}

function mergeCvText(string $manual, string $extracted, string $previous): string
{
    $manual = normalizeLongText($manual, 50000);
    $extracted = normalizeLongText($extracted, 50000);
    $previous = normalizeLongText($previous, 50000);

    if ($manual !== '' && $extracted !== '') {
        if (str_contains($manual, safeSubstr($extracted, 0, 200))) {
            return $manual;
        }
        return normalizeLongText($manual . "\n\n[Extracto automático]\n" . $extracted, 50000);
    }
    if ($manual !== '') {
        return $manual;
    }
    if ($extracted !== '') {
        return $extracted;
    }
    return $previous;
}

function extractTextFromCv(string $tmpName, string $originalName, string $ext): string
{
    $ext = strtolower($ext);

    if ($ext === 'txt' || $ext === 'md') {
        $raw = @file_get_contents($tmpName);
        return normalizeLongText(is_string($raw) ? $raw : '', 50000);
    }

    if ($ext === 'rtf') {
        $raw = @file_get_contents($tmpName);
        if (!is_string($raw)) {
            return '';
        }
        $txt = preg_replace('/\\\\par[d]?/u', "\n", $raw) ?? $raw;
        $txt = preg_replace('/\\\\[a-z]+-?\d* ?/iu', ' ', $txt) ?? $txt;
        $txt = preg_replace('/[{}]/', '', $txt) ?? $txt;
        return normalizeLongText($txt, 50000);
    }

    if ($ext === 'docx') {
        $zip = new ZipArchive();
        if ($zip->open($tmpName) !== true) {
            return '';
        }
        $xml = '';
        $idx = $zip->locateName('word/document.xml');
        if ($idx !== false) {
            $content = $zip->getFromIndex($idx);
            $xml = is_string($content) ? $content : '';
        }
        $zip->close();

        if ($xml === '') {
            return '';
        }

        $xml = str_replace(['</w:p>', '</w:tr>', '</w:tc>'], ["\n", "\n", "\t"], $xml);
        $txt = strip_tags($xml);
        $txt = html_entity_decode((string)$txt, ENT_QUOTES | ENT_XML1, 'UTF-8');
        return normalizeLongText($txt, 50000);
    }

    if ($ext === 'pdf') {
        return normalizeLongText(extractPdfTextSimple($tmpName), 50000);
    }

    return '';
}

function extractPdfTextSimple(string $path): string
{
    $raw = @file_get_contents($path);
    if (!is_string($raw) || $raw === '') {
        return '';
    }

    $textParts = [];

    if (preg_match_all('/\(([^\)]{1,500})\)\s*Tj/s', $raw, $m1)) {
        foreach ($m1[1] as $chunk) {
            if (!is_string($chunk)) {
                continue;
            }
            $textParts[] = decodePdfString($chunk);
        }
    }

    if (preg_match_all('/\[(.*?)\]\s*TJ/s', $raw, $m2)) {
        foreach ($m2[1] as $chunk) {
            if (!is_string($chunk)) {
                continue;
            }
            if (preg_match_all('/\(([^\)]{1,500})\)/s', $chunk, $inner)) {
                foreach ($inner[1] as $piece) {
                    if (is_string($piece)) {
                        $textParts[] = decodePdfString($piece);
                    }
                }
            }
        }
    }

    $joined = implode("\n", $textParts);
    return normalizeLongText($joined, 50000);
}

function decodePdfString(string $value): string
{
    $text = str_replace(['\\n', '\\r', '\\t'], ["\n", ' ', ' '], $value);
    $text = preg_replace('/\\\\\d{3}/', ' ', $text) ?? $text;
    $text = str_replace(['\\(', '\\)', '\\\\'], ['(', ')', '\\'], $text);
    return normalizeLongText($text, 50000);
}

/**
 * @return array{0:bool,1:mixed,2:int}
 */
function supabaseStorageUpload(
    string $baseUrl,
    string $key,
    string $bucket,
    string $objectPath,
    string $tmpFile,
    string $mime
): array {
    $encodedBucket = rawurlencode($bucket);
    $encodedPath = implode('/', array_map('rawurlencode', explode('/', $objectPath)));
    $url = $baseUrl . '/storage/v1/object/' . $encodedBucket . '/' . $encodedPath;

    $binary = @file_get_contents($tmpFile);
    if (!is_string($binary) || $binary === '') {
        return [false, 'No se pudo leer archivo para Storage', 500];
    }

    $ch = curl_init($url);
    if ($ch === false) {
        return [false, 'No se pudo inicializar cURL (Storage)', 500];
    }

    $headers = [
        'apikey: ' . $key,
        'Authorization: Bearer ' . $key,
        'Content-Type: ' . $mime,
        'x-upsert: true',
    ];

    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_CUSTOMREQUEST, 'POST');
    curl_setopt($ch, CURLOPT_HTTPHEADER, $headers);
    curl_setopt($ch, CURLOPT_POSTFIELDS, $binary);
    curl_setopt($ch, CURLOPT_TIMEOUT, 60);

    $raw = curl_exec($ch);
    $errno = curl_errno($ch);
    $error = curl_error($ch);
    $httpCode = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);

    if ($errno !== 0) {
        return [false, 'cURL error Storage: ' . $error, 500];
    }

    $decoded = json_decode((string)$raw, true);
    $payload = $decoded ?? (string)$raw;

    if ($httpCode >= 200 && $httpCode < 300) {
        return [true, $payload, $httpCode];
    }

    return [false, $payload, $httpCode];
}

function buildAnalysisInput(array $profile): array
{
    $fields = [
        'full_name',
        'headline',
        'email',
        'phone',
        'location',
        'linkedin_url',
        'portfolio_url',
        'work_authorization',
        'target_roles',
        'target_industries',
        'target_countries',
        'work_modes',
        'employment_types',
        'seniority_target',
        'salary_expectation',
        'preferred_languages',
        'skills_core',
        'strengths',
        'constraints',
        'notes',
    ];

    $data = [];
    foreach ($fields as $field) {
        $data[$field] = normalizeLongText((string)($profile[$field] ?? ''), 8000);
    }

    $data['cv_es_text'] = normalizeLongText((string)($profile['cv_es_text'] ?? ''), 16000);
    $data['cv_en_text'] = normalizeLongText((string)($profile['cv_en_text'] ?? ''), 16000);

    if ($data['cv_es_text'] === '') {
        $data['cv_es_text'] = '(No disponible)';
    }
    if ($data['cv_en_text'] === '') {
        $data['cv_en_text'] = '(No disponible)';
    }

    return $data;
}

/**
 * @return array{ok:bool,brief?:string,analysis?:string,structured?:array<string,mixed>,error?:string}
 */
function openrouterAnalyzeProfile(
    string $apiKey,
    string $model,
    string $baseUrl,
    array $analysisInput,
    string $appUrl,
    string $appName
): array {
    $url = $baseUrl . '/chat/completions';

    $systemPrompt = <<<TXT
Eres un consultor senior de carrera y estrategia profesional.
Analiza el perfil y CV con enfoque en empleabilidad, oportunidades de negocio y plan de acción.
Debes responder SOLO con un JSON válido (sin markdown, sin texto adicional).
TXT;

    $schemaHint = <<<TXT
Devuelve JSON con esta estructura:
{
  "brief": "resumen ejecutivo en español (max 180 palabras)",
  "analysis": "análisis detallado en español, con subtítulos en markdown",
  "scorecard": {
    "market_fit": 0,
    "cv_quality": 0,
    "role_clarity": 0,
    "growth_potential": 0
  },
  "strengths": ["..."],
  "gaps": ["..."],
  "opportunity_hypotheses": ["..."],
  "action_plan_30_60_90": {
    "first_30_days": ["..."],
    "days_31_60": ["..."],
    "days_61_90": ["..."]
  },
  "recommended_keywords": {
    "linkedin_jobs": ["..."],
    "linkedin_feed": ["..."]
  }
}
TXT;

    $analysisJson = json_encode($analysisInput, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT);

    $userPrompt = <<<TXT
Analiza estos datos del profesional y sus CV (ES/EN):
$analysisJson

$schemaHint
TXT;

    $payload = [
        'model' => $model,
        'messages' => [
            ['role' => 'system', 'content' => $systemPrompt],
            ['role' => 'user', 'content' => $userPrompt],
        ],
        'temperature' => 0.2,
        'max_tokens' => 1800,
    ];

    $headers = [
        'Authorization: Bearer ' . $apiKey,
        'Content-Type: application/json',
        'Accept: application/json',
    ];
    if ($appUrl !== '') {
        $headers[] = 'HTTP-Referer: ' . $appUrl;
    }
    if ($appName !== '') {
        $headers[] = 'X-Title: ' . $appName;
    }

    $ch = curl_init($url);
    if ($ch === false) {
        return ['ok' => false, 'error' => 'No se pudo inicializar cURL para OpenRouter.'];
    }

    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_POST, true);
    curl_setopt($ch, CURLOPT_HTTPHEADER, $headers);
    curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($payload, JSON_UNESCAPED_UNICODE));
    curl_setopt($ch, CURLOPT_TIMEOUT, 90);

    $raw = curl_exec($ch);
    $errno = curl_errno($ch);
    $error = curl_error($ch);
    $httpCode = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);

    if ($errno !== 0) {
        return ['ok' => false, 'error' => 'OpenRouter cURL error: ' . $error];
    }

    $decoded = json_decode((string)$raw, true);
    if ($httpCode < 200 || $httpCode >= 300) {
        return [
            'ok' => false,
            'error' => 'OpenRouter respondió HTTP ' . $httpCode . ': ' . (is_array($decoded) ? json_encode($decoded, JSON_UNESCAPED_UNICODE) : (string)$raw),
        ];
    }

    if (!is_array($decoded)) {
        return ['ok' => false, 'error' => 'OpenRouter no devolvió JSON válido.'];
    }

    $content = extractMessageContent($decoded);
    if ($content === '') {
        return ['ok' => false, 'error' => 'OpenRouter devolvió respuesta vacía.'];
    }

    $parsed = parseJsonFromText($content);
    if (!is_array($parsed)) {
        return [
            'ok' => true,
            'brief' => safeSubstr(normalizeLongText($content, 6000), 0, 500),
            'analysis' => normalizeLongText($content, 32000),
            'structured' => ['raw_text' => normalizeLongText($content, 32000)],
        ];
    }

    $brief = normalizeLongText((string)($parsed['brief'] ?? ''), 6000);
    $analysis = normalizeLongText((string)($parsed['analysis'] ?? ''), 32000);

    if ($analysis === '') {
        $analysis = normalizeLongText(json_encode($parsed, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT), 32000);
    }
    if ($brief === '') {
        $brief = safeSubstr($analysis, 0, 500);
    }

    return [
        'ok' => true,
        'brief' => $brief,
        'analysis' => $analysis,
        'structured' => $parsed,
    ];
}

function extractMessageContent(array $response): string
{
    $content = $response['choices'][0]['message']['content'] ?? '';

    if (is_string($content)) {
        return trim($content);
    }

    if (is_array($content)) {
        $parts = [];
        foreach ($content as $item) {
            if (is_string($item)) {
                $parts[] = $item;
                continue;
            }
            if (is_array($item) && isset($item['text']) && is_string($item['text'])) {
                $parts[] = $item['text'];
            }
        }
        return trim(implode("\n", $parts));
    }

    return '';
}

/**
 * @return array<string,mixed>|null
 */
function parseJsonFromText(string $text): ?array
{
    $clean = trim($text);
    if ($clean === '') {
        return null;
    }

    if (str_starts_with($clean, '```')) {
        $clean = preg_replace('/^```[a-zA-Z0-9_-]*\n?/u', '', $clean) ?? $clean;
        $clean = preg_replace('/\n?```$/u', '', $clean) ?? $clean;
        $clean = trim($clean);
    }

    $decoded = json_decode($clean, true);
    if (is_array($decoded)) {
        return $decoded;
    }

    $start = strpos($clean, '{');
    $end = strrpos($clean, '}');
    if ($start === false || $end === false || $end <= $start) {
        return null;
    }

    $slice = substr($clean, $start, $end - $start + 1);
    $decoded = json_decode($slice, true);
    if (is_array($decoded)) {
        return $decoded;
    }

    return null;
}
