import requests
import urllib.parse
from typing import List
from goose3 import Goose
from bs4 import BeautifulSoup
from datetime import datetime
from .NewsArticle import NewsArticle


class YahooNewsScaper:
    """Class for scraping news articles from Yahoo News using the BeautifulSoup web
    scraping library"""

    def __init__(self):
        """
        Initialises an instance of the YahooNewsScraper class. This constructor sets up
        the base URL required to get articles from Yahoo News.
        """
        self.base_url = "https://news.search.yahoo.com/search"

    def search(
        self,
        query: str,
        page: int = 1,
    ):
        """
        Returns a list of NewsArticle objects, based on the search query and page
        number, scraped from Yahoo News

        Parameters:
        query: Specifies what to search for on Yahoo News
        page: The page number of the search results to return. Each page contains 10
              articles (page numbers are 1-indexed)

        Returns:
            A list of NewsArticle objects
        """
        # Add search query and page number to URL
        url_encoded_query = urllib.parse.quote(query)
        url = self.base_url + f"?q={url_encoded_query}"
        url += f"&b={(10 * (page - 1)) + 1}"

        # Get page and pass to BeautifulSoup
        ynews_page = requests.get(url).text
        doc = BeautifulSoup(ynews_page, "html.parser")
        news_articles = doc.find_all("div", class_="dd NewsArticle")

        # Convert all articles into NewsArticle objects
        articles = []
        for article in news_articles:
            title = article.find("h4", class_="s-title fz-16 lh-20").text
            author = article.find("span", class_="s-source mr-5 cite-co").text
            timestamp = article.find("span", class_="fc-2nd s-time mr-8").text[2:]
            description = article.find("p", class_="s-desc").text
            url = article.find("h4", class_="s-title fz-16 lh-20").find("a").get("href")

            articles.append(
                NewsArticle(
                    title=title,
                    url=url,
                    timestamp=timestamp,
                    description=description,
                    publisher=author,
                )
            )

        return articles

    @staticmethod
    def get_article_text(url: str):
        """
        Helper method that extracts and returns the meta description and main content
        of an article from the specified URL.

        This function uses the Goose library to extract the main text body from
        webpages. It fetches the article at the provided URL and returns a string,
        combining the meta description and the cleaned text of the article.

        Parameters:
        url (str): The URL of the article from which to extract content

        Returns:
        A string combining the meta description and the main cleaned text of the article
        """
        g = Goose()
        article = g.extract(url=url)
        print("ARTICLE TEXT")
        print(article.cleaned_text)
        print("META DESCRIPTION")
        print(article.meta_description)

        return article.meta_description + article.cleaned_text
