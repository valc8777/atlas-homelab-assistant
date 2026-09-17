<?php

require_once '/var/www/html/lib/base.php';

$principal = $argv[1] ?? '';
if ($principal === '') {
    fwrite(STDERR, "missing principal\n");
    exit(1);
}

$db = \OC::$server->getDatabaseConnection();

foreach (['calendars', 'addressbooks'] as $table) {
    $qb = $db->getQueryBuilder();
    $result = $qb->select('id', 'uri', 'displayname', 'principaluri')
        ->from($table)
        ->where($qb->expr()->eq('principaluri', $qb->createNamedParameter($principal)))
        ->executeQuery();
    while ($row = $result->fetch()) {
        echo json_encode(['table' => $table] + $row), PHP_EOL;
    }
    $result->closeCursor();
}
