from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "uid_variables_tenant__e8b401";
        ALTER TABLE "tenant" DROP CONSTRAINT IF EXISTS "tenant_name_key";
        DROP INDEX IF EXISTS "uid_tenant_name_71239f";
        DROP INDEX IF EXISTS "uid_scene_tenant__3c34e7";
        DROP INDEX IF EXISTS "uid_role_tenant__c5545c";
        ALTER TABLE "playbook_version" DROP CONSTRAINT IF EXISTS "uid_playbook_ve_playboo_808e98";
        DROP INDEX IF EXISTS "uid_playbook_tenant__7e8ff3";
        ALTER TABLE "account" DROP CONSTRAINT IF EXISTS "account_phone_key";
        DROP INDEX IF EXISTS "uid_account_phone_9b3340";
        ALTER TABLE "account" DROP CONSTRAINT IF EXISTS "account_username_key";
        DROP INDEX IF EXISTS "uid_account_usernam_c7a6b4";
        ALTER TABLE "account" DROP CONSTRAINT IF EXISTS "account_oauth_id_key";
        DROP INDEX IF EXISTS "uid_account_oauth_i_5d0eca";
        ALTER TABLE "account" DROP CONSTRAINT IF EXISTS "account_email_key";
        DROP INDEX IF EXISTS "uid_account_email_c1479a";
        ALTER TABLE "account" ALTER COLUMN "username" TYPE VARCHAR(255) USING "username"::VARCHAR(255);
        ALTER TABLE "playbook_version" ALTER COLUMN "version" TYPE VARCHAR(50) USING "version"::VARCHAR(50);
        ALTER TABLE "runlog" ALTER COLUMN "tool_version" TYPE VARCHAR(50) USING "tool_version"::VARCHAR(50);
        CREATE UNIQUE INDEX "idx_unique_user_active" ON "account" ("tenant_id", "username") WHERE "deleted" IS FALSE;
        CREATE UNIQUE INDEX "idx_unique_email_active" ON "account" ("tenant_id", "email") WHERE "deleted" IS FALSE;
        CREATE UNIQUE INDEX "idx_unique_phone_active" ON "account" ("tenant_id", "phone") WHERE "deleted" IS FALSE;
        CREATE UNIQUE INDEX "idx_unique_oauth_id_active" ON "account" ("tenant_id", "oauth_id") WHERE "deleted" IS FALSE;
        CREATE UNIQUE INDEX "idx_unique_scene_active" ON "scene" ("tenant_id", "name") WHERE "deleted" IS FALSE;
        CREATE UNIQUE INDEX "idx_unique_tenant_active" ON "tenant" ("name") WHERE "deleted" IS FALSE;
        CREATE UNIQUE INDEX "idx_unique_role_active" ON "role" ("tenant_id", "name") WHERE "deleted" IS FALSE;
        CREATE UNIQUE INDEX "idx_unique_var_active" ON "variables" ("tenant_id", "name") WHERE "deleted" IS FALSE;
        CREATE UNIQUE INDEX "idx_unique_playbook_active" ON "playbook" ("tenant_id", "name") WHERE "deleted" IS FALSE;
        CREATE UNIQUE INDEX "idx_unique_playbook_version_active" ON "playbook_version" ("tenant_id", "playbook_id", "version") WHERE "deleted" IS FALSE;
        """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "runlog" ALTER COLUMN "tool_version" TYPE VARCHAR(255) USING "tool_version"::VARCHAR(255);
        ALTER TABLE "account" ALTER COLUMN "username" TYPE VARCHAR(32) USING "username"::VARCHAR(32);
        ALTER TABLE "playbook_version" ALTER COLUMN "version" TYPE VARCHAR(30) USING "version"::VARCHAR(30);
        CREATE UNIQUE INDEX IF NOT EXISTS "uid_role_tenant__c5545c" ON "role" ("tenant_id", "name");
        CREATE UNIQUE INDEX IF NOT EXISTS "uid_scene_tenant__3c34e7" ON "scene" ("tenant_id", "name");
        CREATE UNIQUE INDEX IF NOT EXISTS "uid_tenant_name_71239f" ON "tenant" ("name");
        CREATE UNIQUE INDEX IF NOT EXISTS "uid_account_email_c1479a" ON "account" ("email");
        CREATE UNIQUE INDEX IF NOT EXISTS "uid_account_oauth_i_5d0eca" ON "account" ("oauth_id");
        CREATE UNIQUE INDEX IF NOT EXISTS "uid_account_usernam_c7a6b4" ON "account" ("username");
        CREATE UNIQUE INDEX IF NOT EXISTS "uid_account_phone_9b3340" ON "account" ("phone");
        CREATE UNIQUE INDEX IF NOT EXISTS "uid_playbook_tenant__7e8ff3" ON "playbook" ("tenant_id", "name");
        CREATE UNIQUE INDEX IF NOT EXISTS "uid_variables_tenant__e8b401" ON "variables" ("tenant_id", "name");
        CREATE UNIQUE INDEX IF NOT EXISTS "uid_playbook_ve_playboo_808e98" ON "playbook_version" ("playbook_id", "version");"""


MODELS_STATE = (
    "eJztXWuP20ay/SvGfMoFJlmK4kuLxQJO4tz13TgO7NnsYuNA4KM5o7VeS0p2BoH/++1qvp"
    "pkk2JTpNiaqQ8xJmQXJZ0udnedev1xs9kFZB1/85JEK//h5s8v/rjZuhtC/6jcuX1x4+73"
    "xXW4cHC9NRvqFmO8+BC5/oFeDd11TOilgMR+tNofVrstvbo9rtdwcefTgavtfXHpuF3990"
    "iWh909OTyQiN749Td6ebUNyO8kzv53/3EZrsg6KH3VVQCfza4vD497du319vADGwif5i39"
    "3fq42RaD94+Hh902H73aHuDqPdmSyD0QePwhOsLXh2+X/s7sFyXftBiSfEVOJiChe1wfuJ"
    "/bEQN/twX86LeJ2Q+8h0/5Wp8ZtuHMLcOhQ9g3ya/YX5KfV/z2RJAh8NPdzRd23z24yQgG"
    "Y4HbJxLF8JVq4H334EZi9DiRCoT0i1chzABrwzC7UIBYKM5AKG7c35drsr0/gILrptmC2S"
    "8v3333t5fvvqKj/gd+zY4qc6LjP6W39OQeAFsACa+GBIjp8OsEcKZpHQCkoxoBZPfKANJP"
    "PJDkHSyD+H/v3/4kBpETqQD5jy39gb8GK/9w+2K9ig+/qQlrC4rwq+FLb+L4v2sevK/evP"
    "xXFdfvfnz7LUNhFx/uI/YU9oBvKcawZIYfuZcfLniu//GzGwXL2p2dvmsaW7+10TfVK+7W"
    "vWdYwS+G35dtIr6/O24Pwv0lvdW+wXCDTu0wNx+OTqBbH47mPLQ/HC1Xn9G/bcf7cAxDzY"
    "frmkn/NXR6xSDEhH81+rfpm4SO13QbRs7cD0fb1B16RZ/bMF4LPhznmgZPWwTz8pWF5lJZ"
    "2/Nm2RVLN+gzLXvuZt8klfV8+t1sR0tHvn15PDy8eP19LmfpIft2MGrhzLJvkdx1FoFO/9"
    "Vt/aaiY8/oZw98Umhep4VHhZ7L9Omzws1fwuPWBy1+kb4bnhuTb/6ydjde4P71rzdDreBz"
    "q8MCPrca12+41X6woL+C/lFD+nuKF9xpWNAzoQrgQSr1TfbHRZdz+l6Z+gzeEzqO6qkZUg"
    "1dmKFRfQEbkI+IG7zdrh/TGW9B/u71m1fv716++bm08n//8u4V3GGav3msXP2qOkv5Q178"
    "8/Xd317A/77499ufXlU3iHzc3b9v4Du5x8Nuud19XroBp5zZ1Qyu0p597DPJR1Un2bJCA6"
    "bX057xJKdfvphjugwRwL82y9/udmvibsWTzElVptmjYmPNbONxwNR1OqkLy4IJdjS6Jzl0"
    "N+o2tS1T+e3btz+WZvHb13eVJfMfb759Rc/CbProoNUhMU1Tw6yAmR5k3e1hKbc7lYR6bV"
    "KpAkihfEOxI6apB1/7uu1/bcwC42vHnXlfO77phXMnCMnMqW9VdBoWJHAAeteALT4IkyPG"
    "6+87vmPjW3HHmETsb4lJ4GWu054bBcpgFe/X7uNSFs6q3MXUWnlEycZdrWWgzAWuEsOZ2Y"
    "llMFtYBrPGMuwpDlLqmAtcKYadIGxBsAagG8efd5HUNsXL4ApZ0B+hu6QbOPxEucNVWXDy"
    "89UQdNeAJylA5yN5lFFQTuQqX/NOK2XLQllfJwERj379HpqZiaFeliHdUSvrQfKAz8tcpW"
    "aOdbSEnydtk3JiqJyVbZ1EmxW1JKWcWCUhVM/clAcIJGDMxl/uaMRs1sHI5FFA9CPiHnaR"
    "DI6cyLTKWCGMDeK5fRiOud6FpNebSXq9ZkH+vl9RAIQO/3bStiw5AHM7HNh3u49k++HohD"
    "44vexZKM/eXglbmyHSysmv3fiwXO/uV3R+erDzAnGlZhumWNOYDxL8jpYNr1loms9v0iUc"
    "+7VldUk+EYChfoBK5X/4+zuybnrfU9/kq/wZyi+sXzI1z67eCDwdxz1odHC92PAOrIGxYX"
    "qTY7OkzyOxeB+RR+k1/7CpvLqj6tITwWtE/drE98uI+GT1iQjOfDIYvSFx7N5f3nkcOBAx"
    "o1lw/Ji7bD+aWwytxcA4xYR+v+tHyZwHLH6Ixf4Mi1K2WoEjiZr2H8/D6mfuKU9kecqAWT"
    "ITPoZ43niZGYXnI/Vz8dQLb4FF4NqI6nTm2SBD6ZcikPqJqFW26z0NoC5wnPpMvIfhcPpn"
    "8rQnpFAZTtFutzkPo3fpEy6qQPo8BEjm3tDvGLXgLgbJoBYKQwRsdKowM8cE2iagyuM4hk"
    "//9bU5hAcFJlj2jjHych779Ief+eq9h2c8oRcuW8GvFpuxF2365iVRd+dhc5c/42lwJ9k7"
    "BXygyC8nA032iCf2Sl0rNCO+UZ/caAVfOT5zpfkle86VY3NmAlYBbLRbi1bvN+728W4H/z"
    "Ly/zX9fHfri0j+/Iiw7rWCj50W24TgbfrNl5UUs+x3RKBK9F3MszaTrLIEql3EUIZAoBTC"
    "NPIin4D0ViqV3j08RLvj/QN/I0o/jgKaBMLDze9evv/u5feM/l/W8Cy9FnRWkvPdMFPY7+"
    "Cr3hQmv0NqCnebvlOYz8EZ8+ju99EO0qUHmMaXybM2pNOpYajZhJ3Rc4Ggc+ZOssSB6810"
    "zlvomua48iNrU13CszzXbiEqPeWJ6JLXpE4zXtvP8J3t9s4C0zjUC8tkzp+6Ddl4IvpTeu"
    "46n+yHfEcDlswbmtSItl3bzUxsS59pY76vxW+tzjAHZ3mKSylLnSc5lSqe2n2ST2SlA4g8"
    "XS3MT68Oum3PVIfhPLPOxndIWjd118mm0bKNOWSsmRqwI1ZtAjsMxyRpTJLGJGlMkh7N4n"
    "22k4xJ0pgkfQLlJ5wknR5wZBN7K2ITJwJWzk9JSRt7EWqqwcx/bXm0K9JqgW7N/RA8gAqB"
    "fqALr7Rml4QUgJhZg+bMtpXU6wZuurnMWy4wVZG3m19/E67Ulq0tyqaXpVt25s1OPNjN5b"
    "qkN9LBSsJVK15IzUYucI2z0TOeYNTZUKVAH8e9ikiQMjXbQn9UBnbgPVJaOVm2OLq5qGAn"
    "YD9OC33Ywh0TsoI8z0xK2yEvgrwI8iLIiyAvgrwI8iLIiwxqO64OolI+LTOQCYxlM/Zy8i"
    "cqvnAWHZ2GY5eiiY5bSc0uJBQwxmc2xDRrpsFinbXEHumptV2w1Zux1bsXXb8jvzc0T2gu"
    "uj4Furze5mxSFltuepJZZcId99W/7trtvHzD/fHtT/+bDa8af2Xc44N7OArM7saWFYXA6b"
    "YVQ4GunULc1j3IcGQZfFBhnAVBknnigYa0fDYJCw0WbEOzkkGz8qCFBvXGk+INcFsv37Z0"
    "oE9sEgQdX5hB+mXUQriW4poujdNVFVNp0nxWMiEvCz+rAj6D+uueTYTzYSTl4entaeaD1S"
    "EhvSpbVERVMyD4OcJaJunbdwxWB3o3kHM0laTU2qQc3YH1TPMW/Y4AQ3Ev9cwNV3AKOEG4"
    "lCSVfp8WxMoSzhMi+Dm/VXs3ckVhqs2cfyExAOk/ZEaTMyNAAnsLlrZjLGCSYcuyTLujw+"
    "vS/hZ6i/4kOf9XITKZz+WPL0KbnD/cOR5wX1CLSU3kfXe9BlfJ8gAFw2Q2lLqkApvKAs5p"
    "C43AQdsKSJI9m39B6ULRXfaVWfO+MqvtK+GarkpydjsnogDAnOFuMkeiZev+dHt2zU1Yhr"
    "qO8w+7iKzut38nXSPXp65LcQbgLYHrkfs5d+TxGiaMIP8ymu91pKygoivaE80IKn6gVDZQ"
    "KZGglmZQyxUaIhsoOWi1+ti/223D1f2NwL2e3rlt86z7xZgOTvWEWEkCHhYzA6KAQos0Jh"
    "OcGo5Oc3Sao9McneboNEenOTrN0Wk+lNNcslPLOV1aBjwC8yekhWN39DJeAM5P7voo2B9e"
    "bf3ocU+RaWnrnklOb3rz6JravJfRbWidmpTDsEZ8k5sTNncYkirlQS0ct8ooLj19H+WiZw"
    "qJ6TXW0g0t76OdBiCbHWPMRwJXkbByVrL6XWK6v2eg3QjMX8Go2zZTmJXVXmaMQFxIdIo2"
    "d1j/czhuijmnRiNZRhQNZjSY0WBGgxkNZjSY0WBGg3mwxmi7bbASZ4O3Bu0WQpObInxepm"
    "Vbs+RIdbaqjxKpm5w08/4tktovlp58AvhzbNKsxJxZ5qprjO0l0vD3K19qmckEFMB27iXp"
    "E0n2vTKYJt9TqMDNsUclIcWCvwqrq6vmXjrkKHn9ZctJlKUmV+h6jF3JDFavwMT1JldYlg"
    "6NkQwdgA2TNodwJWMZXIffPDsCPnAE/vYINRhl4OUkeuE76Jox92B1nvlBEiTK/g6nQZI+"
    "3j9GEdn6AhdII5oVqekRJbaVH+YmRjRleST2t0JCtc2NU9SiiIU625xK9HLy80SU8olSrU"
    "WnT2niWJ/Z3Wji0kAkhZEURlIYSWEkhZEURlIYSeGhTN7JiIZBFzLFeYUErn2080kcL5tY"
    "hlNw1+Unr/5Swp3L6DsvTGj4SjAJgPHuGPk99LyQm5xSK/HvtgXNwslCHUUPdp+3650bLI"
    "/RWgbnqpxaOOvMz2EYjMbUoOloaNMlZmGEJPGCKIM/hVHUiqaZxcgFJiIxOPvPO67Wh9U2"
    "/gY+T2D4AaEcQF0LJ9DnWf5YGGrgLZlr8CboxOfL5sKcOT5fupWNP7+wz4iMf3zcbNxIwO"
    "o1e2hrgmq9PAsoW+EsnI47waW9tOvd/RJMehnEeZnLnX1Erwq8oOJXxTJZedswsDNy1Zpb"
    "HUvvoKd8RE95UhwJKiFBLmxyXBKPFbOE04WPrHdxv/pJZckBSJBB6XIWjbswSfC8a7tkPa"
    "rlXr6y1OQvXb9mzGMXSuKaE3bFlROZHlQuXp2eokIplnBkaFm3eUlseRmlwDXtObDtltXV"
    "CToyuKz/uPSSUJaaHOB+jbYvXIeFqWQdZOlCLO+z51yJBnetvsK/suLyK9X1dgAwO/bSVW"
    "Wp7Yolt7WchjJ9mQdAs3vBmenf/q5Qlpe602imp6WrRbPf8aormuWz5IWrLHWIFXmdmZg3"
    "jUEjxZDb09Ejhc3KRkvGkfB2YueYkmYhjC/B+BKML8H4EowvwfgSjC/B+JJhQx8Kdr5fpE"
    "ldfnJeofFYpWr4SYFh8lu8s+aBf4ayc6Fgm2A+oiRBTHoSKsJqoZ/HpPD9QpRBn37aNg53"
    "0SaWxl4gOjnyTmixZtiWriTadC+lvzloDHhrzFSqC06erNSUYGcQDSwYkvSRypaejlMwcB"
    "LTerX9SFGjv80/ivP8GxEXiSqLuUpJjfFjDOEgRYXlzl6oquDEq0nyfeg64hIAfU7myqwj"
    "4WpNlnuXfoAEviWhyVdqvoaeZTp2tl86AUM7dHr5U8cpBEl8YGFlD+oVsUEQ71tmM/kuSp"
    "7Fk/edHqTDXR3d5jjNithU0ZoNzVz4cwiv6Qa1WyEMXNIdeLGYy1Rnmxbv5umoCSqVA5yp"
    "/9VOxTJyBW1JOk5HJqzYO2LZOmtXGXhZznupWC1XB5K3YKHn2DVOZ3aCfyDu+vDQJ+Wl6Q"
    "GTb+XiEz/UG2b/Es9WskxufrQ/Y06aHzH5rIhtAuVnhX7iJxId+s9J4wOmn5GGQ4HiMxIR"
    "aFLNKE73UYa0qAtezIC2hRtOHvUP20WQVAOBZJmFvpDpczi0xbyPdv+hO5rQMdMIblloEG"
    "DPaGOVR2n98mPWFhRq+cPfhHQuhjd0UaCpArhHiwtQJ4J7skjY0dzxyobCThEjp4AGDxkk"
    "N3UApwJKO2QE5zQxh29IHLtsi6jFGma3bttiDFmY7ybu3PXPCE04rukzh/VpBMwDx0lszK"
    "I1Rd27JCmKcYYYZ4hxhhhniHGGGGeIcYYYZzggkQb0S30OWlsbZCIK2GbMKZw6ALjTkzlz"
    "gC3zvPPLloxSu2G1oSfRZQ/0a4LTz4FphazFnWFf1xxIh7KdE782rNZzKBcBbEmJjP3RW6"
    "+gUEYIi46jGe4+Wn2iXwxWIH+WXPpT/BgfyAakQypnkyD8k7vdUkPVZ13amXPGT6o39Vqr"
    "upQi05tLkem1UmSreBkR312vpXfmiqQKuzMfoWUZME+mFZxfZWbA3ZmCtl9tt33ALuRUgz"
    "pvH2tlFbCSGlcGcUj2whR6P1DVqwEnhaoxWX0isnxqRUyt5StxBVvm3GKWSK/Gvk+i9Mh4"
    "CCetUu20mluaHJ+9AY4OBa8MAgUIii1DRvdHr1VCKACyOl8SUms+zHlAd+GFps2m1vi2yh"
    "oMvyumv88GvHONDV7TOhTZSBfjpwFtv9W7c8mN8salWgWTq12xByt50uzi4Zwv8f3SLTS1"
    "cpZMhX/4O+tFLc6gKLtvJtf68zD+cgHnVwZRsw+MA/G0K4yfvi4uMV6VDaKRcinZJAoksT"
    "sbSm/IPwDdY+geQ/cYusfQPYbuMXSPoXtsKPdYevKRnIay1PTsQ3F6UgbY620Zu7CgaYLj"
    "Mb9JHoef+F5YrB2ZJ+fSPHeJDYXbs/JtMwj15HbHaRk48nmT2AKSyl2Wml65ZQ2zCxNpmy"
    "Lo8ExWggtfVBnfruRCWY9UCiH9mUSbVRxTBOI3ZHu8EZjR1SG3bXb0vhi83GSju9jRtgEl"
    "9i2TLivOPAC31Nys1choHYh2MdrFaBejXYx2MdrFaBejXTyU+cZ2XSmrIRO41BFBuHj1PA"
    "WM0/OR+2YSQFbEpk1XVwlP7khex7O5BkpFTKWCNJ3RvXSVEmGX0maIxc1JEVoRtN5eZjFI"
    "Rl/lGjDXu1hWerNlpQuJl4kYg7X7SM9rH29EVEF277aVI+BHdeAGSoWP2pJMmwciN4DcAH"
    "IDyA0gN4DcAHIDyA0MZYjJVsA9q/TtwDtUcVZSsAZuRDZu9LEObXOyaCExPbiyTSUunpG7"
    "PZBo6wos2/aEN05MhVW6VPydJeBWK6WqtGJj8TUsEDYuyl3DEc7rosmVaUwN+eW+jY+UyX"
    "vICIQKUznNxnge6KV3P0fqE4kGhOmX5GkXhUiWTuwIDBhwIqNBApW77BFTdbYeSnPOpO84"
    "I8G9F2jaG3f7eLeDfzsugnfuvYqOq5bljn3xZYWdTH9GBIpEgmVVBROkdhHD+CN5TAFMV8"
    "kc/fROrrjJ7cNDtDveP5RVOvk84fpKry9reJajRqGf+RCz17Xhu/Lzl/8QmRnk28L3mMM4"
    "+0yJWexEn/M7XAuTXtkIT5Pq1b1YmmDn4+vEKWunhiPZjmQ7ku1ItiPZjmQ7ku1Itg/FCb"
    "eyDM0hOXuMepIOzdl7ksqeC6jAUMqZ4ePX11qTWLrAViEzfZeeRQC5e7qtqwHoMSaRLKC8"
    "zOSAnpejOjp7vvcGIM75ULALEQ8Dkre3FcY8X946dNMATbuo52FK1ewKIP/+dalttRZRXz"
    "2KW627GCpTLpbd61MVO4JSCaQVV0QLl8V5KzrwWJ+40ZIclq0bjnTAaLMQ8lnIZyGfhXwW"
    "8lnIZyGfhXzWYP1IHtztPVnCF6/PQ0tPkrLY5NasOQeck/XM8diqZmnnF/gfJfCRO1R2VX"
    "tOZHpuiz+kmvPQTmot0SseBJxanj5jLTHiB6gBHQbQd28GpXPNGVP/mZZtOTZx6XUzNM3s"
    "EaX2APzHWAG9YhG3V80hs0tzDLO5OYZZa45xvdW0ytZGVk3rlhWyMaAzsDsPb2dZraykkr"
    "dJtHlH3AeumBWu6cYNJ6PdUVDMuBHtqtjlMJ9rJ1F3wkDLur47gWNmR6/iAJa9DVBUCN4e"
    "1pOcmvQTzsF/YtGC1ezvKAkN4O3oNRk3f3wRbspWYMxA0zUv6aUEX/ZDqQ95hj9UnjMN3S"
    "PBPYnhT8ffUjM1PntjGcVhgsHd4/H9lTCtzi6pstjE6E7MnWCf8rGwbXOkcD6QCd0p04V6"
    "31bdKOVX8rQjABM5sNP7tXR65yop7/fR7hNrLnhmosfL4kkX19wxMht4ZfxMvIc8aPvsVJ"
    "h/Jk+7/Pst8iANiFK+ZEbH7Xp3fx5Y7/JnTJ4iMyBcl3BvZurV4t7kNLCDe/MzN1rKvZl+"
    "TGfPZsN4dGqqcTBHp+Yz8nehU/MZTDI6NdGpeQLlJ+zUTM819GDxkUj52WqCk1bPzb4OVX"
    "xTT1rzabzis+4mBjvE9gozn3U5Psyajw+zGq/XWmO32ZusVI3dKyidg0T/BYj+1Ofel/Av"
    "i6uAdn+rdyLKetkYKdGbup6iWMlIDM1tE69d1jzktyctVDQmL8QyCwRkUJZx0NZpOB3Rgf"
    "Upkgn4SgsCuqd5IPI8yPMgz4M8D/I8yPMgz4M8zzOvfNzzFIBdka4AT6wPMmq4a4016BIK"
    "IypLt8xMoPOjPuTqxKqY5z1csc+0E/sQJSPPIBNUKxrJ/ZRq2ciiykC5ZGS5pX21aCRI1Q"
    "tGZjKZancqF5nPfwvNsNvcCGkGev22nWZIR3SgGYzQ9Fl6EDRqZ0cOetgIs4SI1jgTGVGk"
    "IpCKQCoCqQikIpCKQCoCqYhnTkUIT0/ntWIaPts6Ooq98M3mdCExkSV98+tvAj1OvhY7FN"
    "jiyuaYSDrgrsyMAIPMazbDM0t6HDQ0irOsknZXThh4eT67r0GahRdAGyzLMZQvM3n1sQ+y"
    "Sv6UsvquSa8vnf4H1MuSfDo/++9V/oyJ0rWKmTmT5OTVdRPfLzN2qj82b0gcu/eXNzcDB3"
    "RPs9gx2NCA3Uq1MeQqiMCBWScmWx2g/k4IZztHM1x2d6EKZdxQqxb54ga+eLfpwxfT1aDG"
    "F7OrPGPfs0sUkv6Sk0hRiJZDzmQmkz94SPo/ydcVOQDyTN4WF0AxpoMTIFnNLFtbwNrl6H"
    "TV0rxFtudaLIo23ZE9INugdpjAIdD3MQLnwK+cLQmlf/g/MxueWlK7Y+SnF36r+BN+LfyP"
    "uUD+yLQu2G/odECnAzod0OmATgd0OqDTAZ0O3ZwOTcR4S0+fJmJ8Cn42rxdbLoqZnNKKlN"
    "d+gA/P3zI2RRJvXmZy7tYgjgf/hlbyhaaHlDtRd3acFSLTq3BhXKiipCXDRApTdTySvMl2"
    "ni9yHFcvoMXgkYU4E1ILYtu3PXFs3TQqfKBHLmkVLglNvtKaAfCu5sy25Q9wF9BgBlaPMv"
    "RVOaVwPi+pffioBQZWsIqB/emnzlVhpeA2iB6A4enYSi7SLuOlpHGviE2PuO46eYiOegtJ"
    "CldfLW8QHwl1iQaOBeiKq/n1NqLgWfiiEUVHYAduceBG91LZWtl4ldK0KpCaMwuW6rkvlx"
    "p0sRAzegt+TA305iJRhcTkq7ITLuYAMfDrNgnmcOxY+LdAWgDqljOHGQi1MOuAk7iizDmB"
    "slL0ytlzMkpZKfAeHpZ9SPKy5ABM+aB7aAhl08yF78kT5VdCjGdgtLo/yDboNbm8nGJTm7"
    "18ZvjMp/Z34h+F4Xct1CQnM/mKWvLJa5qpCp1Wdu1LoFsTnBxiJzDAnUQW2rkn2RGL7snC"
    "XBOc3nYoKr0paC8oVN1QxfL+o1ObdIuUTQDgZdQC2lvk8SUGCT1mOHe14C4b5s8wrIMuHZ"
    "t+R58TXCnkXSPQeX07HfevRK1OFZeSrnB3Kdv5/tXdi5/+8eOPUxWafO9T/G8EEaDJjdu2"
    "ANA4H9Kl1iQXBmDaELVuWSzKva38QzchLPyAMZgYg4kxmBiDiTGYGIOJMZhY+IE/KylIVU"
    "Rk40aCtrVtnFsmMT24si1E0Fd8aqfVmZrqFvs3zDKwoVP7rH5JA2V2ZsmljjMwsFP5aRTR"
    "ULNJC/YPxwoZ42rwU6qToYDSXrr8BWOdnkb9i+KYdh7Uw1VsyCjLIRL+M7J3HHbsshn//G"
    "+ppfyDQorS/XP+V5zvnyhyLeE/l8rp1YGy/e9cYao/XL5to3kPbvckf2ZN255NThC74oEf"
    "tlpW0oTn5/nB6aEwHVSLxYdBSAkjJYyUMFLCSAkjJYyUMFLCSAlXT1wKUsLipNtG/rIh3f"
    "bi7KXwvNpwRr0cV1njfEaLLUHL8RzLkVpWPexGKtViNabW2lA2I9tAbkRmY3LnttVyLMZ0"
    "MB6TNyXJzbNd283qXdqeC2y/odX2485Cz8YchIqbaA5ObimgOXiD5uCzmGQ0B6cxBy9qiA"
    "xfaKK+YStok0zm5h+2zIQqXn7FPNHTgzykI/oCFt6GbDxRqs0VVAJvWXcsfcayJk25hj1D"
    "1ArnAJWpE14i9TgTML1ePFWi5nu7EchSo0Q2YJYz1WIC5kMwRwQdgrdoAT4n4wAtwGcwyW"
    "gBokPwBMroEFTQIVgkmStpfF+pQ7BS/S6vv5vnLiRewVI9tybXYXlQVrjwcm5EuWIA3UuL"
    "KMKLDFVLZPj6soUDcLs/CirpNZcvrEsqVsiQhz2rtZeVM4RDlN1xBWqBfJSihmk9U+kJqc"
    "qpNh1cBC1fG/Wapua6k9+EtVIru4U5N82snQYvUA40MSy2iM1CftA0u8UB1N4XzEnLybQQ"
    "mf5sVJoVz7Qg2dCqtaObZnNoKq/aWrBOlfKqJWDz8qpqABsffZ/E8RKgkIG3KqcAyIzS1h"
    "eQNzt3erXnnnWBd9YM76wGL7U84ad2X6YLgcst0zMhnsIE5dsX+fpc5CPX1uRs9DTrcHNd"
    "2Jbioc01YSdYic8pAzv2gkE/h0Rbdy3JdPFiKlBdJfVm/dHt0CKDVKQbmQLLDtdSFFhJaP"
    "K1upQOKjyM99H14dPyseTBBUrk9qzYqqIq52a+GhBjVYmLxfLsuYD3M4N5JPIAVFHkrjE9"
    "lZf3dHWJZNsaANQ7esZ4mT/sCre6rhCXNvrTAGM1FKyGcn3VUJKSztFxu97dC+wQiWoo7/"
    "JnPKEK2OdWRmmPiwNrTRQWl1pxLVFx2YjOaVFpI8Q8qwlINKvGKnQYjoFwGAiHgXAYCIeB"
    "cBgIh4FwGAh3lYFwo4VAqBcH1yPeSqkwK2EbbwhpCHv5JUcItnLpgyXQzcZPDm3SuZhaSh"
    "ZLfAGXLz3qK6O41JjcLWXBLQlNjPCP9LsoiSz/PSWwrYhNrr+l6o1YU73XsU3cf5tz6Kbt"
    "ibmoSyO083rqpufMX8z+nAx7ocEfJuk6BeMUVe/hXlRDmzk7F9qcKqPHjGkU5e2e8HsRNX"
    "DlTUulcH1w44flJ3d9lDr2lqWmRzc/Q8AXo/qrzXt5bEdB+AnG11gm/G3NNRYipnmw59mB"
    "Stb0Bj6P/icJOi+mGuiloCayMGQi8i5EYbgC501zUkE6XOFcgnI1bvVyBjZCDW9etjdi1Z"
    "4iyCZJySBzfvGedMmuxYKMVovkpDcujWto8MkVUQ/tnrllEWsh66Djc2gknXWnRNFxh447"
    "dNyh4w4dd+i4Q8cdOu4GK7QAJx5Z711JaPpD8VW48Jbbo7iA3klPHic5OdZqOvGaoqNPJe"
    "araEb3C4e+mOncHN9/uiyFgnDbJJhlsae25RhlZwk9BNti6226yZCwt2upcnk5xv5xwnnR"
    "xyeUOjB6mPA7Eu+OkZ+qg4CbKAbcnqQnotJYSYbCCQw4EJKF1omVEA1HJgKZCGQikIlAJg"
    "KZCGQikIm4ciZi2L1KbSriesuV8gdRBYGlD19tJevsZBKTczqlQqPhDGrNGX5Hz/0FsC3Z"
    "O2V4X2396HFPQWqtflZIq6XHi5kRJIEp/Vg0rRuPprUxadpzKGuUlukiScMP2ADT7hChFv"
    "JTotKZBONhMR72hJ00Oa4Khf7kBNobuHZzimZLRt1259qWm1ziDMYtWXYsG0LwJdk3kSgy"
    "ccjEIROHTBwyccjEIROHTBwycVfDxF15Xj+fyGRqLCeBeCzxA9oWWDZgn4ZTCCsAKDMPLe"
    "RSczxLG6fUJ6BlNIapMBrOXuSfb++Ok0nWZuiDijuWx5Kpuf+FlOqFbRL+YkfVHzi9+oG4"
    "68PD0n8gviB0qxHvqphCqJ9egpLU9nwYmw2DwFZQXLzcbChClvziRiuYF2EoUnHzto0a+V"
    "Qa1oUPmUFzGtM3wHszh9PPYuaXmvQmnRPgrmayGfZgrljPZpYna/rwFlmabsPImVt+TrLB"
    "z1nrHP4634Ctfheynb/KXs2FA0NBwWChAjWa+xlDTL/aInvAqbqgT//XIuuErBOyTsg6Ie"
    "uErBOyTsg6PdXgpNoJoXGDqpzNlEG0oTxRM7HRVJnoYqTGza+/CZW3epBVk85gcMksIen4"
    "yek8sRWhjB5j+56Lte/Bvh0j9u0Yntf58v+0B15R"
)
