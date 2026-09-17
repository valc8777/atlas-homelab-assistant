<?php

require_once '/var/www/html/lib/base.php';

use OCA\DAV\DAV\Sharing\Backend;
use OCA\DAV\DAV\Sharing\SharingMapper;
use OCA\DAV\CalDAV\Sharing\Service as CalendarSharingService;
use OCA\DAV\CardDAV\Sharing\Service as AddressbookSharingService;

$action = $argv[1] ?? '';
$type = $argv[2] ?? '';
$resourceId = isset($argv[3]) ? (int)$argv[3] : 0;
$principal = $argv[4] ?? '';

if (!in_array($action, ['share-read', 'unshare'], true)) {
    fwrite(STDERR, "action must be share-read or unshare\n");
    exit(1);
}

if (!in_array($type, ['calendar', 'addressbook'], true)) {
    fwrite(STDERR, "type must be calendar or addressbook\n");
    exit(1);
}

if ($resourceId <= 0) {
    fwrite(STDERR, "resource id must be positive\n");
    exit(1);
}

if ($principal === '' || !str_starts_with($principal, 'principals/users/')) {
    fwrite(STDERR, "principal must start with principals/users/\n");
    exit(1);
}

$mapper = new SharingMapper(\OC::$server->getDatabaseConnection());
$service = $type === 'calendar'
    ? new CalendarSharingService($mapper)
    : new AddressbookSharingService($mapper);

if ($action === 'share-read') {
    $service->shareWith($resourceId, $principal, Backend::ACCESS_READ);
} else {
    $service->deleteShare($resourceId, $principal);
}

echo json_encode([
    'ok' => true,
    'action' => $action,
    'type' => $type,
    'resource_id' => $resourceId,
    'principal' => $principal,
    'access' => $action === 'share-read' ? 'read' : null,
]), PHP_EOL;
