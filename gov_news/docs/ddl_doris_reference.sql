-- ============================================================================
-- Doris 方言原始样本（由项目所有者提供，原文存档，勿改）
--
-- 用途：MySQL 表结构的对齐基准。本项目实际落地库是 MySQL 8.0，方言映射与逐字段
--       对照见同目录 ddl_mysql.md；MySQL 建表实现在 gov_news/db.py。
--
-- 样本表 ods_au_defence_news 对应源 key = au_defence（该源尚未加入 sources.json，
-- 本表仅作为「每源一张表 ods_<source_key>_news」的表结构样板）。
-- ============================================================================

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- ----------------------------
-- Table structure for ods_au_defence_news
-- ----------------------------
DROP TABLE IF EXISTS `ods_au_defence_news`;
CREATE TABLE `ods_au_defence_news`  (
  `doc_id` varchar(64) NULL DEFAULT NULL COMMENT 'md5(url)',
  `source` varchar(32) NULL DEFAULT NULL COMMENT 'au_defence',
  `publish_date` date NULL DEFAULT NULL,
  `title` varchar(512) NULL DEFAULT NULL,
  `url` varchar(512) NULL DEFAULT NULL,
  `column_name` varchar(128) NULL DEFAULT NULL,
  `publisher` varchar(256) NULL DEFAULT NULL,
  `summary` text NULL,
  `content` text NULL,
  `content_length` int NULL DEFAULT NULL,
  `match_level` varchar(16) NULL DEFAULT NULL,
  `matched_keywords` varchar(1024) NULL DEFAULT NULL,
  `source_page` varchar(512) NULL DEFAULT NULL,
  `raw_path` varchar(512) NULL DEFAULT NULL,
  `http_status` int NULL DEFAULT NULL,
  `crawl_time` datetime NULL DEFAULT NULL,
  `batch_time` datetime NULL DEFAULT NULL,
  `image_urls` array<text> NULL COMMENT '图片 MinIO URL 数组(限量3张)'
) ENGINE = Doris CHARACTER SET = utf-8 COLLATE = utf-8 COMMENT = '澳大利亚国防部，南海行动通报' PARTITION BY UNPARTITIONED (NULL)
PARTITIONS 1
SUBPARTITION BY NULL (NULL)
SUBPARTITIONS 1
(PARTITION `ods_au_defence_news` MAX_ROWS = 0 MIN_ROWS = 0 (SUBPARTITION `NULL` MAX_ROWS = 0 MIN_ROWS = 0 ))
;

SET FOREIGN_KEY_CHECKS = 1;
