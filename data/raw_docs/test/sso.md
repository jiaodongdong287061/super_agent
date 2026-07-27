以其他项目（如 app1）接入为例：

  ---
  前置准备

  在授权中心的 OAuth2 客户端管理中注册你的项目：

  POST http://localhost:8081/auth/oauth2/client/save
  {
    "clientId": "my-app",
    "clientSecret": "my-secret",
    "redirectUris": ["http://my-app.com/callback"]
  }

  ---
  第一步：跳转到授权中心

  其他项目将用户浏览器重定向到网关：

  http://localhost:8081/auth/oauth2/authorize?client_id=my-app&redirect_uri=http://my-app.com/callback&response_type=code

  第二步：浏览器端流程

  用户访问上面 URL
      ↓
  Gateway（8081）→ 白名单放行 → StripPrefix → auth 服务
      ↓
  SsoAuthenticationFilter 检查 SSO_SESSION Cookie
      ├── 有 Cookie 且有效 → 直接认证 → 下发授权码
      └── 无 Cookie → 重定向到前端登录页
                      http://localhost:80/login?redirect=...(原 OAuth2 URL)
                      ↓
                   用户输入账密 → POST /auth/login → 登录成功
                      ↓
                   拿到 JWT + SSO_SESSION Cookie（种在 localhost:80）
                      ↓
                   handleLogin 检测到 redirect → 跳回 auth/oauth2/authorize
                      ↓
                   这次带上 Cookie → 认证通过 → 下发授权码

  第三步：拿到授权码

  浏览器 302 重定向到你的项目：

  http://my-app.com/callback?code=AUTH_CODE_xxx

  第四步：换取 Token

  你的项目后端用授权码调接口换 token：

  POST http://localhost:8081/auth/oauth2/client/token
  {
    "code": "AUTH_CODE_xxx",
    "clientId": "my-app",
    "clientSecret": "my-secret"
  }

  返回：

  {
    "access_token": "eyJ...(Ruoyi JWT)",
    "token_type": "Bearer",
    "expires_in": 43200,
    "refresh_token": "xxxx"
  }

  第五步：后续请求

  其他项目的 API 请求在请求头携带 access_token：

  GET http://other-project/api/xxx
  Authorization: Bearer eyJ...


AutheFilter逻辑：
        Claims claims = JwtUtils.parseToken(token);
        if (claims == null)
        {
            return unauthorizedResponse(exchange, "令牌已过期或验证不正确！");
        }
        String userkey = JwtUtils.getUserKey(claims);
        boolean islogin = redisService.hasKey(getTokenKey(userkey));
        if (!islogin)
        {
            return unauthorizedResponse(exchange, "登录状态已过期");
        }
        String userid = JwtUtils.getUserId(claims);
        String username = JwtUtils.getUserName(claims);
        if (StringUtils.isEmpty(userid) || StringUtils.isEmpty(username))
        {
            return unauthorizedResponse(exchange, "令牌验证失败");
        }
    
package com.aiCloud.common.core.utils;

import java.util.Map;
import com.aiCloud.common.core.constant.SecurityConstants;
import com.aiCloud.common.core.constant.TokenConstants;
import com.aiCloud.common.core.text.Convert;
import io.jsonwebtoken.Claims;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.SignatureAlgorithm;

/**
 * Jwt工具类
 *
 * @author ruoyi
 */
public class JwtUtils
{
    public static String secret = TokenConstants.SECRET;

    /**
     * 从数据声明生成令牌
     *
     * @param claims 数据声明
     * @return 令牌
     */
    public static String createToken(Map<String, Object> claims)
    {
        String token = Jwts.builder().setClaims(claims).signWith(SignatureAlgorithm.HS512, secret).compact();
        return token;
    }

    /**
     * 从令牌中获取数据声明
     *
     * @param token 令牌
     * @return 数据声明
     */
    public static Claims parseToken(String token)
    {
        return Jwts.parser().setSigningKey(secret).parseClaimsJws(token).getBody();
    }

    /**
     * 根据令牌获取用户标识
     * 
     * @param token 令牌
     * @return 用户ID
     */
    public static String getUserKey(String token)
    {
        Claims claims = parseToken(token);
        return getValue(claims, SecurityConstants.USER_KEY);
    }

    /**
     * 根据令牌获取用户标识
     * 
     * @param claims 身份信息
     * @return 用户ID
     */
    public static String getUserKey(Claims claims)
    {
        return getValue(claims, SecurityConstants.USER_KEY);
    }

    /**
     * 根据令牌获取用户ID
     * 
     * @param token 令牌
     * @return 用户ID
     */
    public static String getUserId(String token)
    {
        Claims claims = parseToken(token);
        return getValue(claims, SecurityConstants.DETAILS_USER_ID);
    }

    /**
     * 根据身份信息获取用户ID
     * 
     * @param claims 身份信息
     * @return 用户ID
     */
    public static String getUserId(Claims claims)
    {
        return getValue(claims, SecurityConstants.DETAILS_USER_ID);
    }

    /**
     * 根据令牌获取用户名
     * 
     * @param token 令牌
     * @return 用户名
     */
    public static String getUserName(String token)
    {
        Claims claims = parseToken(token);
        return getValue(claims, SecurityConstants.DETAILS_USERNAME);
    }

    /**
     * 根据身份信息获取用户名
     * 
     * @param claims 身份信息
     * @return 用户名
     */
    public static String getUserName(Claims claims)
    {
        return getValue(claims, SecurityConstants.DETAILS_USERNAME);
    }

    /**
     * 根据身份信息获取键值
     * 
     * @param claims 身份信息
     * @param key 键
     * @return 值
     */
    public static String getValue(Claims claims, String key)
    {
        return Convert.toStr(claims.get(key), "");
    }
}

package com.aiCloud.common.core.constant;

/**
 * Token的Key常量
 * 
 * @author ruoyi
 */
public class TokenConstants
{
    /**
     * 令牌前缀
     */
    public static final String PREFIX = "Bearer ";

    /**
     * 令牌秘钥
     */
    public final static String SECRET = "abcdefghijklmnopqrstuvwxyz";

}


生成token逻辑：
     public Map<String, Object> createToken(LoginUser loginUser)
    {
        String token = IdUtils.fastUUID();
        Long userId = loginUser.getSysUser().getUserId();
        String userName = loginUser.getSysUser().getUserName();
        loginUser.setToken(token);
        loginUser.setUserid(userId);
        loginUser.setUsername(userName);
        loginUser.setIpaddr(IpUtils.getIpAddr());
        refreshToken(loginUser);

        // Jwt存储信息
        Map<String, Object> claimsMap = new HashMap<String, Object>();
        claimsMap.put(SecurityConstants.USER_KEY, token);
        claimsMap.put(SecurityConstants.DETAILS_USER_ID, userId);
        claimsMap.put(SecurityConstants.DETAILS_USERNAME, userName);

        // 接口返回信息
        Map<String, Object> rspMap = new HashMap<String, Object>();
        rspMap.put("access_token", JwtUtils.createToken(claimsMap));
        rspMap.put("expires_in", TOKEN_EXPIRE_TIME);
        return rspMap;
        }

package com.aiCloud.common.core.constant;

/**
 * 权限相关通用常量
 * 
 * @author ruoyi
 */
public class SecurityConstants
{
    /**
     * 用户ID字段
     */
    public static final String DETAILS_USER_ID = "user_id";

    /**
     * 用户名字段
     */
    public static final String DETAILS_USERNAME = "username";

    /**
     * 授权信息字段
     */
    public static final String AUTHORIZATION_HEADER = "Authorization";

    /**
     * 请求来源
     */
    public static final String FROM_SOURCE = "from-source";

    /**
     * 内部请求
     */
    public static final String INNER = "inner";

    /**
     * 用户标识
     */
    public static final String USER_KEY = "user_key";

    /**
     * 登录用户
     */
    public static final String LOGIN_USER = "login_user";

    /**
     * 角色权限
     */
    public static final String ROLE_PERMISSION = "role_permission";
}
