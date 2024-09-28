import os

DISCORD_OAUTH2_CLIENT_ID = os.getenv("DISCORD_OAUTH2_CLIENT_ID")
DISCORD_OAUTH2_CLIENT_SECRET = os.getenv("DISCORD_OAUTH2_CLIENT_SECRET")
DISCORD_API_BASE_URL = os.environ.get("API_BASE_URL", "https://discordapp.com/api")
DISCORD_AUTHORIZATION_BASE_URL = DISCORD_API_BASE_URL + "/oauth2/authorize"
DISCORD_TOKEN_URL = DISCORD_API_BASE_URL + "/oauth2/token"
DISCORD_OAUTH2_REDIRECT_URI = (
    "https://coin-pulse-ffda7bc3f791.herokuapp.com/callback_discord"
)

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_AUTHORIZATION_BASE_URL = "https://accounts.google.com/o/oauth2/auth"
GOOGLE_TOKEN_URL = "https://accounts.google.com/o/oauth2/token"
GOOGLE_REDIRECT_URI = "https://coin-pulse-ffda7bc3f791.herokuapp.com/callback_google"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v1/userinfo"
GOOGLE_SCOPE = ["profile", "email"]

MAIL_USERNAME = os.getenv("MAIL_USERNAME")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD")

TOKEN_GENERATOR_SECRET_KEY = os.getenv("TOKEN_GENERATOR_SECRET_KEY")
TOKEN_GENERATOR_SALT = os.getenv("TOKEN_GENERATOR_SALT")
TOKEN_GENERATOR_EXPIRATION_TIME_SECONDS = 3600

COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY")
COINGECKO_API_HEADERS = {
    "accept": "application/json",
    "x-cg-demo-api-key": COINGECKO_API_KEY,
}

POSTGRESQL_USERNAME = os.getenv("POSTGRESQL_USERNAME")
POSTGRESQL_PASSWORD = os.getenv("POSTGRESQL_PASSWORD")

REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
REDDIT_SECRET_KEY = os.getenv("REDDIT_SECRET_KEY")
REDDIT_USERNAME = os.getenv("REDDIT_USERNAME")
REDDIT_PASSWORD = os.getenv("REDDIT_PASSWORD")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT")

OPEN_TRADE_UPDATE_INTERVAL_SECONDS = 60
WALLET_VALUE_UPDATE_INTERVAL_SECONDS = 1800

MOST_COMMON_PASSWORDS = [
    "password",
    "123456",
    "123456789",
    "guest",
    "qwerty",
    "12345678",
    "111111",
    "12345",
    "col123456",
    "123123",
    "1234567",
    "1234",
    "1234567890",
    "000000",
    "555555",
    "666666",
    "123321",
    "654321",
    "7777777",
    "123",
    "D1lakiss",
    "777777",
    "110110jp",
    "1111",
    "987654321",
    "121212",
    "Gizli",
    "abc123",
    "112233",
    "azerty",
    "159753",
    "1q2w3e4r",
    "54321",
    "pass@123",
    "222222",
    "qwertyuiop",
    "qwerty123",
    "qazwsx",
    "vip",
    "asdasd",
    "123qwe",
    "123654",
    "iloveyou",
    "a1b2c3",
    "999999",
    "Groupd2013",
    "1q2w3e",
    "usr",
    "Liman1000",
    "1111111",
    "333333",
    "123123123",
    "9136668099",
    "11111111",
    "1qaz2wsx",
    "password1",
    "mar20lt",
    "987654321",
    "gfhjkm",
    "159357",
    "abcd1234",
    "131313",
    "789456",
    "luzit2000",
    "aaaaaa",
    "zxcvbnm",
    "asdfghjkl",
    "1234qwer",
    "88888888",
    "dragon",
    "987654",
    "888888",
    "qwe123",
    "football",
    "3601",
    "asdfgh",
    "master",
    "samsung",
    "12345678910",
    "killer",
    "1237895",
    "1234561",
    "12344321",
    "daniel",
    "000000",
    "444444",
    "101010",
    "fuckyou",
    "qazwsxedc",
    "789456123",
    "super123",
    "qwer1234",
    "123456789a",
    "823477aA",
    "147258369",
    "unknown",
    "98765",
    "q1w2e3r4",
    "232323",
    "102030",
    "12341234",
    "147258",
    "shadow",
    "123456a",
    "87654321",
    "10203",
    "pokemon",
    "princess",
    "azertyuiop",
    "thomas",
    "baseball",
    "monkey",
    "jordan",
    "michael",
    "love",
    "1111111111",
    "11223344",
    "123456789",
    "asdf1234",
    "147852",
    "252525",
    "11111",
    "loulou",
    "111222",
    "superman",
    "qweasdzxc",
    "soccer",
    "qqqqqq",
    "123abc",
    "computer",
    "qweasd",
    "zxcvbn",
    "sunshine",
    "1234554321",
    "asd123",
    "marina",
    "lol123",
    "a123456",
    "Password",
    "123789",
    "jordan23",
    "jessica",
    "212121",
    "7654321",
    "googledummy",
    "qwerty1",
    "123654789",
    "naruto",
    "Indya123",
    "internet",
    "doudou",
    "anmol123",
    "55555",
    "andrea",
    "anthony",
    "martin",
    "basketball",
    "nicole",
    "xxxxxx",
    "1qazxsw2",
    "charlie",
    "12345qwert",
    "zzzzzz",
    "q1w2e3",
    "147852369",
    "hello",
    "welcome",
    "marseille",
    "456123",
    "secret",
    "matrix",
    "zaq12wsx",
    "password123",
    "qwertyu",
    "hunter",
    "freedom",
    "999999999",
    "eminem",
    "junior",
    "696969",
    "andrew",
    "michelle",
    "wow12345",
    "juventus",
    "batman",
    "justin",
    "12qwaszx",
    "Pass@123",
    "passw0rd",
    "soleil",
    "nikita",
    "Password1",
    "qweqwe",
    "nicolas",
    "robert",
    "starwars",
    "liverpool",
    "5555555",
    "bonjour",
    "124578",
]

PASSWORD_ALLOWED_SPECIAL_CHARS = [
    "~",
    "`",
    "!",
    "@",
    "#",
    "$",
    "%",
    "^",
    "&",
    "*",
    "(",
    ")",
    "-",
    "_",
    "+",
    "=",
    "{",
    "}",
    "[",
    "]",
    "|",
    "\\",
    ";",
    ":",
    '"',
    "<",
    ">",
    ",",
    ".",
    "/?",
    "/",
]
