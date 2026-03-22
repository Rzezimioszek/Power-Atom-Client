# -*- coding: utf-8 -*-
import logging
from lxml import etree
from typing import List, Optional, Tuple

logger = logging.getLogger("PowerAtom")

class AtomLink:
    def __init__(self, rel: str, url: str, title: str = "", mime_type: str = ""):
        self.rel = rel
        self.url = url
        self.title = title
        self.mime_type = mime_type

    def __repr__(self):
        return f"<AtomLink(rel={self.rel}, url={self.url})>"

class AtomNode:
    def __init__(
        self, 
        title: str, 
        node_type: str, 
        url: Optional[str] = None,
        links: Optional[List[AtomLink]] = None,
        bbox: Optional[Tuple[float, float, float, float]] = None,
        updated: Optional[str] = None,
        summary: Optional[str] = None,
        rights: Optional[str] = None
    ):
        self.title = title
        self.url = url
        self.node_type = node_type
        self.links = links or []
        self.bbox = bbox
        self.updated = updated
        self.summary = summary
        self.rights = rights

    def __repr__(self):
        return f"<AtomNode(title={self.title}, type={self.node_type})>"

class AtomParser:
    @staticmethod
    def _strip_ns(tag: str) -> str:
        if '}' in tag:
            return tag.split('}', 1)[1]
        return tag

    @staticmethod
    def _parse_georss(element: etree._Element) -> Optional[Tuple[float, float, float, float]]:
        local_name = element.tag.split('}')[-1] if '}' in element.tag else element.tag
        text = element.text or ""
        try:
            nums = [float(x) for x in text.strip().replace('\n', ' ').replace(',', ' ').split()]
            if not nums: return None
            if local_name == "box" and len(nums) == 4:
                return (nums[1], nums[0], nums[3], nums[2])
            elif local_name == "polygon" and len(nums) >= 6 and len(nums) % 2 == 0:
                y_coords = nums[0::2]
                x_coords = nums[1::2]
                return (min(x_coords), min(y_coords), max(x_coords), max(y_coords))
        except (ValueError, IndexError):
            pass
        return None

    def parse(self, xml_content: bytes, source_url: str) -> List[AtomNode]:
        nodes = []
        try:
            parser = etree.XMLParser(recover=True, remove_blank_text=True)
            root = etree.fromstring(xml_content, parser=parser)
        except Exception as e:
            logger.error(f"Failed to parse XML from {source_url}: {e}")
            return []

        # Extract feed-level metadata as fallback
        feed_title = root.xpath("string(//*[local-name()='feed']/*[local-name()='title'])")
        feed_rights = root.xpath("string(//*[local-name()='feed']/*[local-name()='rights'])")

        # Find all entries (robust XPath handles root being entry or inside feed)
        entries = root.xpath(".//*[local-name()='entry'] | self::*[local-name()='entry']")
        
        for entry in entries:
            title = entry.xpath("string(*[local-name()='title'])") or "Untitled"
            updated = entry.xpath("string(*[local-name()='updated'])")
            summary = entry.xpath("string(*[local-name()='summary'])")
            rights = entry.xpath("string(*[local-name()='rights'])") or feed_rights
            
            bbox = None
            georss_elements = entry.xpath("*[local-name()='box' or local-name()='polygon']")
            if georss_elements:
                bbox = self._parse_georss(georss_elements[0])
            
            links = []
            # Standard links
            link_elements = entry.xpath("*[local-name()='link']")
            for le in link_elements:
                links.append(AtomLink(
                    rel=le.get("rel", ""),
                    url=le.get("href", ""),
                    mime_type=le.get("type", ""),
                    title=le.get("title", "")
                ))
            
            # Support for content src (some feeds use this for direct data links)
            content_elements = entry.xpath("*[local-name()='content' and @src]")
            for ce in content_elements:
                links.append(AtomLink(
                    rel="enclosure", # Treat content src as enclosure
                    url=ce.get("src", ""),
                    mime_type=ce.get("type", ""),
                    title=title
                ))

            nodes.append(AtomNode(
                title=title.strip(),
                node_type="entry",
                links=links,
                bbox=bbox,
                updated=updated,
                summary=summary,
                rights=rights
            ))
        
        # If no entries found, maybe it's a very simple feed or just links at root
        if not nodes:
            # Check for root links if no entries
            root_links = root.xpath("*[local-name()='link']")
            if root_links:
                nodes.append(AtomNode(
                    title=feed_title or "Main Feed",
                    node_type="entry",
                    links=[AtomLink(l.get("rel",""), l.get("href",""), l.get("title",""), l.get("type","")) for l in root_links],
                    rights=feed_rights
                ))

        return nodes
